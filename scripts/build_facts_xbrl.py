#!/usr/bin/env python3
"""Structured extraction for US filers, via SEC's XBRL companyfacts API.

The exchange eval found 36 of 45 US tickers are iXBRL: pdftotext renders
them as taxonomy URLs ("http://fasb.org/us-gaap/2024#OtherAssetsNoncurrent")
rather than statements, so the text extractor yields nothing and no regex
ever could.

The same data is published already typed. A companyfacts response carries
value, exact period, unit and source form per fact, which makes this
strictly better than parsing text: no unit ambiguity, no period guessing,
no adjudication between competing candidates.

Deliberately NO model fallback. A concept absent from XBRL is genuinely
untagged, so a model reading the PDF would be inventing a number rather
than recovering one. Missing stays NULL, and the gap is logged.

Usage:
  build_facts_xbrl.py PYPL [--show]
  build_facts_xbrl.py --check PYPL      # is this ticker covered?
"""

import argparse
import datetime
import json
import pathlib
import sys
import urllib.request
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import schema

REPO = pathlib.Path(__file__).resolve().parents[1]
CACHE = REPO / "state" / "xbrl_cache"
UA = "swilliams@intellum.com stock-research"

TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Schema column -> us-gaap concepts, in preference order. Filers tag the
# same line differently (PayPal uses Revenues; most modern filers use
# RevenueFromContractWithCustomer...), so each maps to a list and the
# first with data wins.
CONCEPTS = {
    # RevenueFromContractWithCustomerIncludingAssessedTax sits last (lowest
    # preference): it names the less precise, sales-tax-inclusive figure,
    # so a filer that also tags the Excluding variant should keep using
    # that one. Ball Corporation (CIK 0000009389) tags ONLY the Including
    # variant for FY2018 onward -- Excluding has just 6 sparse quarterly
    # rows in 2017-2018 with no annual coverage, Revenues doesn't exist for
    # this filer, and SalesRevenueNet stops at FY2017 -- so without this
    # entry BALL's revenue is NULL for every period FY2018-2025.
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "shareholders_equity": ["StockholdersEquity",
                            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "shares_outstanding": ["WeightedAverageNumberOfDilutedSharesOutstanding",
                           "CommonStockSharesOutstanding"],
    "stock_based_comp": ["ShareBasedCompensation",
                         "AllocatedShareBasedCompensationExpense"],
    "dividend_per_share": ["CommonStockDividendsPerShareDeclared"],
    # Not in core_metrics but the DCF wants them; land in kpis.
    "_ebitda_da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
    "_interest_income": ["InvestmentIncomeInterest"],
    "_buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "_equity_award_taxes": ["PaymentsRelatedToTaxWithholdingForShareBasedCompensation"],
    "_cash_taxes": ["IncomeTaxesPaidNet"],
    "_deferred_revenue": ["ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"],
}

# Columns that are a SUM of components rather than a preference pick.
#
# Every entry in CONCEPTS answers "which tag did this filer use for this
# line?" -- one of them is right and the rest are absent. Debt is a
# different question: a filer reports the current portion and the
# non-current portion under separate tags and BOTH are right, so the
# column is their total. ADBE tags LongTermDebt 4.80B alongside
# DebtCurrent 1.84B; picking either alone understates a DCF input.
#
# Within a component, the inner list is still preference-ordered -- filers
# rename their long-term tag over time (ADBE moved off
# LongTermDebtNoncurrent after 2015) -- so each component resolves to one
# value per period, and the components are then added.
SUM_CONCEPTS: dict[str, list[list[str]]] = {
    "total_debt": [
        ["LongTermDebt", "LongTermDebtNoncurrent",
         "LongTermDebtAndCapitalLeaseObligations"],
        ["DebtCurrent", "LongTermDebtCurrent",
         "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    ],
}


def fetch(url: str, dest: pathlib.Path | None = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if dest:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return json.loads(data)


def cik_for(ticker: str) -> int | None:
    """Resolve ticker -> CIK. Only bare US symbols exist in SEC's map."""
    if "." in ticker:
        return None
    cache = CACHE / "company_tickers.json"
    try:
        m = json.loads(cache.read_text()) if cache.exists() else fetch(TICKER_MAP, cache)
    except Exception:
        return None
    for v in m.values():
        if v.get("ticker", "").upper() == ticker.upper():
            return int(v["cik_str"])
    return None


def fiscal_year_end_month(facts: dict[str, Any]) -> int | None:
    """Which month closes this filer's year, read off its annual durations.

    A bare instant fact says nothing about the fiscal calendar, but a ~365-day
    duration does: the month it ends in IS the year end. Taking the most
    common such month tolerates the odd 52/53-week straggler.
    """
    months: dict[int, int] = {}
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    for entry in gaap.values():
        for rows in (entry.get("units") or {}).values():
            for f in rows:
                start, end = f.get("start"), f.get("end")
                if not start or not end:
                    continue
                try:
                    if (_d(end) - _d(start)).days > 300:
                        m = int(end[5:7])
                        months[m] = months.get(m, 0) + 1
                except (ValueError, IndexError):
                    continue
    if not months:
        return None
    return max(months, key=lambda m: (months[m], m))


def _near_fiscal_year_end(end: str, fy_end_month: int,
                          tolerance_days: int = 7) -> bool:
    """Is this instant within a few days of the filer's year-end boundary?

    Measured against the boundary between fy_end_month and the next month,
    in both directions, so a year end that drifts forward (Adobe's
    2021-12-03 for a November close) and one that drifts back (a
    January-closing filer ending 2024-12-28) both read as annual.
    """
    import datetime as dt
    d = _d(end)
    # The month boundary the fiscal year closes on: midnight ending
    # fy_end_month, expressed in the calendar year the instant sits near.
    for year in (d.year - 1, d.year, d.year + 1):
        if fy_end_month == 12:
            boundary = dt.date(year + 1, 1, 1)
        else:
            boundary = dt.date(year, fy_end_month + 1, 1)
        if abs((d - boundary).days) <= tolerance_days:
            return True
    return False


def period_label(fact: dict[str, Any],
                 fy_end_month: int | None = None) -> str | None:
    """Derive the period from the fact's own dates, not its `fy`.

    A 10-K restates prior years, so `fy` is the filing's year rather than
    the fact's. start/end are authoritative.

    An instant (balance sheet) is a position AT a date, and only the one
    falling on the fiscal year end is the annual balance sheet. Labeling all
    of them FY{year} put Reddit's 30-Jun-2026 balance sheet into a phantom
    FY2026 row carrying no revenue, while the real Q2 2026 row carried
    revenue and no balance sheet -- and, per concept, every non-year-end
    quarter was dropped by the rank tie-break. Without `fy_end_month` the
    caller has no fiscal context, so the old annual labeling stands.
    """
    end = fact.get("end", "")
    start = fact.get("start")
    if not end:
        return None
    year = end[:4]
    if not start:                       # instant (balance sheet)
        month = int(end[5:7])
        if fy_end_month is None or month == fy_end_month:
            return f"FY{year}"
        # A 52/53-week filer closes on a fixed WEEKDAY near a month end, so
        # its year end drifts a few days either side of the boundary and
        # some years land in the neighbouring month. Adobe closes on the
        # Friday nearest Nov 30: FY2021 ended 2021-12-03 and FY2022 on
        # 2022-12-02, and an exact month test labeled both Q4 -- leaving
        # those years with no annual balance sheet at all (ADBE lost
        # equity, cash and debt for 4 of 19 years).
        #
        # The window is days rather than a month: a Nov-closing filer's Q1
        # ends in early March, and anything wide enough to catch that would
        # give every filer a phantom second FY row.
        if _near_fiscal_year_end(end, fy_end_month):
            return f"FY{year}"
        q = (month - 1) // 3 + 1
        return f"Q{q} {year}"
    days = (_d(end) - _d(start)).days
    if days > 300:
        return f"FY{year}"
    if days > 200:
        # 9-month 10-Q YTD. It is not a canonical reporting period -- and
        # labeling it H2 collided with the genuine second half, which it
        # overwrote when filed later -- but it is the only place Q3 cash
        # flow exists, because a 10-Q reports cash flow year to date.
        # Keep it under a name no real period can take; decumulate() reads
        # it and drop_scaffolding() removes it before the write.
        return f"9M-{year}"
    if days > 150:
        half = "H1" if int(end[5:7]) <= 8 else "H2"
        return f"{half}-{year}"
    q = (int(end[5:7]) - 1) // 3 + 1
    return f"Q{q} {year}"


def _d(s: str) -> datetime.date:
    import datetime as dt
    return dt.date(int(s[:4]), int(s[5:7]), int(s[8:10]))


# A real filer's raw share count in the "shares" unit is never this
# small -- the smallest genuine value seen across every cached ticker is
# ~862,000 (a micro-cap). Agilent's FY2009 10-K (accn 0001047469-09-010861,
# filed 2009-12-21) tagged WeightedAverageNumberOfDilutedSharesOutstanding
# with unit "shares" but reported the number already in millions (406,
# 371, 346 instead of 406000000, 371000000, 346000000) -- an early-XBRL
# filer tagging error, not a unit this module chooses. Most periods
# self-heal because a later filing restates the same period correctly and
# "later filing wins" picks that one, but a period with no correcting
# duplicate (FY2007 rolls off the 10-K's 3-year comparative window before
# Agilent re-tags it) kept the bare value, which the caller's /1e6 scaling
# then turned into 0.000406 -- 1e-06x the real ~406 million shares.
#
# The same bug class recurs at a different scale: Ball Corporation's
# FY2010 10-K (accn 0000009389-11-000014, filed 2011-02-28) tagged the
# same concept in THOUSANDS instead of raw shares (194038, 189978 instead
# of 194038000, 189978000). Both values clear a 100,000 floor, so that
# threshold let a thousands-scaled tag straight through. FY2009 self-heals
# from a later, correctly-tagged 10-K, but FY2008 rolls off the
# comparative window first and kept the bare 194038, which /1e6 scaling
# turned into 0.194038 million shares -- 1e-03x the real ~194 million
# (319.9m FY2008 net income / 194.038m shares = $1.65, matching Ball's
# reported $1.67 diluted EPS). Raised to the ~862,000 floor already cited
# above as the smallest genuine value seen across every cached ticker,
# which rejects this case too; nothing legitimate in the warehouse falls
# between 100,000 and 862,000 raw shares (only FIG's own known
# shares-scale bug did, in that same gray zone).
MIN_PLAUSIBLE_SHARE_COUNT = 862_000

# Concepts that are a superset BY CONSTRUCTION -- tax/fee-inclusive tags
# name (a strictly narrower concept) + (tax or fee) -- so they are almost
# always the larger of the two whenever a filer tags both for real. The
# same-accession "larger value wins" rule in collect() below exists for the
# opposite shape (a *preferred* concept naming an accidental narrow subset,
# e.g. AVB's ancillary-fee mistagging) and would otherwise let one of these
# override a correct, higher-preference total just because tax-inclusive
# is bigger. Brown-Forman (CIK 0000014693) tags
# RevenueFromContractWithCustomerExcludingAssessedTax $2,994m (net revenue,
# the correct headline figure) alongside
# RevenueFromContractWithCustomerIncludingAssessedTax $3,857m (gross,
# including excise tax) for the same FY2017 period in the same accession;
# letting the larger value win would silently replace 56 periods of correct
# net revenue with the tax-inclusive gross figure.
NEVER_OVERRIDES_SAME_ACCESSION = {"RevenueFromContractWithCustomerIncludingAssessedTax"}


def collect(facts: dict[str, Any],
            concepts: list[str]) -> tuple[str | None, dict[str, Any], str]:
    """Best value per period, merged across every listed concept.

    Filers switch tags over time -- PayPal reports Revenues for 2018-2025
    and RevenueFromContractWithCustomer... only for 2016-2019. Taking the
    first concept that has *any* data would silently drop half the
    history, so merge them and let concept order break ties within a
    period.

    A different case: a filer that tags BOTH concepts for the SAME period
    in the SAME accession -- not a switch-over, but the preferred concept
    naming a narrower line item. AVB's 10-Ks tag
    RevenueFromContractWithCustomerExcludingAssessedTax as ancillary fee
    income ($5.6m) alongside Revenues as the real total ($2,045m) for the
    same FY2016, in the same filing. List-order preference alone would
    take the $5.6m figure for every period the filer dual-tags. Two
    concepts landing on the same period from the same accession is the
    signal a switch-over never produces (each concept there comes from a
    DIFFERENT accession), so when that happens the larger value wins
    regardless of concept order -- unless the challenger is listed in
    NEVER_OVERRIDES_SAME_ACCESSION, whose members are a superset by
    construction and so would otherwise win this comparison for the wrong
    reason.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out: dict[str, tuple[Any, Any]] = {}
    accn_seen: dict[tuple[Any, str], dict[str, Any]] = {}
    used: list[str] = []
    unit_seen: str | None = None
    fy_end = fiscal_year_end_month(facts)
    for pref, concept in enumerate(concepts):
        entry = gaap.get(concept)
        if not entry:
            continue
        got = False
        for unit, rows in entry.get("units", {}).items():
            unit_seen = unit_seen or unit
            for f in rows:
                if (unit == "shares" and f.get("val") is not None
                        and 0 < abs(f["val"]) < MIN_PLAUSIBLE_SHARE_COUNT):
                    continue
                p = period_label(f, fy_end)
                if not p:
                    continue
                # Same accession already reported a value for this period
                # under a different concept: not a switch-over (those span
                # different accessions), so this is one concept naming a
                # subset of another. Keep whichever value is larger.
                accn = f.get("accn")
                prior = accn_seen.get((accn, p)) if accn else None
                if prior is not None and prior["concept"] != concept:
                    if (concept in NEVER_OVERRIDES_SAME_ACCESSION
                            or abs(f["val"]) <= abs(prior["val"])):
                        continue
                    # The new value supersedes the smaller same-accession
                    # figure already recorded for this period.
                    out[p] = (f["val"], prior["rank"])
                    if accn:
                        accn_seen[(accn, p)] = {"val": f["val"], "concept": concept,
                                                 "rank": prior["rank"]}
                    if concept not in used:
                        used.append(concept)
                    continue
                # Prefer an annual statement over a 10-Q restatement, a
                # later filing over an earlier one, and an earlier-listed
                # concept over a later one.
                #
                # "Later filing wins" is what makes per-share history
                # survive splits. Netflix's 10-for-1 means FY2024 EPS is
                # tagged 19.83 in the 2024 10-K and 1.98 in the 2025 one;
                # taking the newer value keeps the whole EPS series on
                # today's share basis, so it stays comparable year to year
                # and divides into the current quoted price. A figure that
                # disagrees with contemporaneous headlines is expected
                # here, not a bug.
                rank = (f.get("form") == "10-K", f.get("filed", ""), -pref)
                if p not in out or rank > out[p][1]:
                    out[p] = (f["val"], rank)
                    got = True
                    if accn:
                        accn_seen[(accn, p)] = {"val": f["val"], "concept": concept,
                                                 "rank": rank}
        if got:
            used.append(concept)
    if unit_seen == "shares":
        _reject_scale_outliers(out)
    return ("+".join(used) if used else None,
            {p: v[0] for p, v in out.items()},
            unit_seen or "USD")


# The absolute MIN_PLAUSIBLE_SHARE_COUNT floor only catches a mis-scaled
# value small enough to look like noise (Agilent's millions-scaled 406).
# ConocoPhillips (CIK 1163165) tagged WeightedAverageNumberOfDilutedShares-
# Outstanding in THOUSANDS for ten straight annual periods (FY2010-2019:
# 1491067 instead of 1491067000) -- a share count that, read as raw shares,
# is ~1.1-1.6 million and clears the 862,000 floor easily, because COP is a
# large-cap and its thousands-scaled value is bigger than a small-cap's
# genuine raw count. Unlike Agilent/Ball, no later filing ever re-tags these
# periods correctly, so "reject the unhealed value" alone does not apply --
# every one of the ten years is unhealed. What marks them is comparison
# against the OTHER resolved periods in the same series (FY2007-2009 and
# FY2020-2025, correctly tagged at ~1.1-1.6 BILLION): a period sitting at
# roughly 1e-3x (thousands) or 1e-6x (millions) of the series' own median is
# the same filer mistagging, just never self-healed by a later accession.
_SCALE_SLIP_RATIOS = (1e-3, 1e-6)
_SCALE_SLIP_TOLERANCE = 0.25  # +/-25% around the exact 1e-3 / 1e-6 ratio


def _reject_scale_outliers(out: dict[str, tuple[Any, Any]]) -> None:
    """Drop shares-unit values that sit at ~1e-3x/1e-6x the majority scale.

    A plain median is not safe here: ConocoPhillips mistagged TEN of its
    sixteen resolved years, so the median itself lands on a mis-scaled
    value. Instead, cluster periods by order of magnitude (round(log10(v)))
    and treat the LARGEST cluster with at least 2 members as the genuine
    scale -- the century of periods across the whole warehouse establishes
    that a filer restates its true share count far more often than it
    mistags one, so the biggest consistent cluster wins regardless of
    whether it happens to be a minority of this one concept's rows. Needs
    at least 5 resolved periods total before trusting any of this -- with
    fewer, a genuine outlier (a buyback, a split) could be mistaken for a
    scale slip.
    """
    vals = [abs(v[0]) for v in out.values() if v[0] is not None and v[0] > 0]
    if len(vals) < 5:
        return
    import math
    clusters: dict[int, list[float]] = {}
    for v in vals:
        clusters.setdefault(round(math.log10(v)), []).append(v)
    eligible = {k: vs for k, vs in clusters.items() if len(vs) >= 2}
    if not eligible:
        return
    best_magnitude = max(eligible, key=lambda k: len(eligible[k]))
    genuine_scale = sum(eligible[best_magnitude]) / len(eligible[best_magnitude])
    for period in list(out.keys()):
        val = out[period][0]
        if val is None or val <= 0:
            continue
        ratio = abs(val) / genuine_scale
        for slip in _SCALE_SLIP_RATIOS:
            if abs(ratio - slip) <= slip * _SCALE_SLIP_TOLERANCE:
                del out[period]
                break


def collect_sum(facts: dict[str, Any],
                components: list[list[str]]) -> tuple[str | None, dict[str, Any], str]:
    """Add together several independently-resolved components per period.

    Each component is a preference-ordered concept list resolved by
    collect(), exactly as a normal column would be; the results are then
    summed per period. A period present in only one component is that
    component's value -- a filer with no current portion still has debt --
    but a period present in NO component stays absent, so an untagged
    column lands NULL rather than a fabricated zero.
    """
    used: list[str] = []
    totals: dict[str, Any] = {}
    unit_seen = "USD"
    for concepts in components:
        name, values, unit = collect(facts, concepts)
        if name is None:
            continue
        used.append(name)
        unit_seen = unit
        for period, val in values.items():
            totals[period] = totals.get(period, 0) + val
    return ("+".join(used) if used else None, totals, unit_seen)


# Cash-flow columns are reported year-to-date in a 10-Q, so a discrete
# Q2 or Q3 is never tagged. These are the columns that need subtracting.
CUMULATIVE_COLUMNS = ("operating_cash_flow", "capex")

# Cumulative spans that exist only to be subtracted, and the pair that
# yields each discrete quarter.
DECUMULATE = {
    "Q2": ("H1-{year}", "Q1 {year}"),
    "Q3": ("9M-{year}", "H1-{year}"),
}


def decumulate(rows: dict[str, dict[str, Any]]) -> None:
    """Derive discrete Q2/Q3 cash flow from the cumulative spans.

    A 10-Q's income statement covers the quarter but its cash flow
    statement covers the year to date: Q2's span is six months and Q3's is
    nine. SEC therefore has no fact for "cash from operations in Q2", and
    the XBRL path left operating_cash_flow, capex and free_cash_flow empty
    for every Q2 and Q3 in a filer's history -- 54 cells on ADBE that the
    CSV it replaced had populated.

    Q2 = H1 - Q1 and Q3 = 9M - H1. A directly tagged quarter is never
    overwritten (some filers do report the discrete period), and a missing
    predecessor leaves the quarter NULL rather than silently equal to the
    cumulative span.
    """
    years = {p.split()[-1].split("-")[-1] for p in rows}
    for year in years:
        for quarter, (whole_key, prior_key) in DECUMULATE.items():
            whole = rows.get(whole_key.format(year=year))
            prior = rows.get(prior_key.format(year=year))
            if whole is None or prior is None:
                continue
            target = rows.setdefault(f"{quarter} {year}", {})
            for col in CUMULATIVE_COLUMNS:
                if target.get(col) is not None:
                    continue
                a, b = whole.get(col), prior.get(col)
                if a is None or b is None:
                    continue
                target[col] = a - b


def drop_scaffolding(rows: dict[str, dict[str, Any]]) -> None:
    """Remove the 9M-YYYY spans kept only so Q3 could be decumulated.

    They are not reporting periods -- nothing downstream should chart or
    screen on them, and periods.py does not parse them.
    """
    for period in [p for p in rows if p.startswith("9M-")]:
        del rows[period]


def derive(rec: dict[str, Any], da: float | None = None) -> None:
    """Fill the columns SEC never tags: FCF, EBITDA and the margins.

    XBRL carries only what a filer marked up, and nobody marks up
    "free cash flow" or "net margin" -- they are conventions, not line
    items. The agent-adjudicated text path has always computed them, so
    without this the two paths produce different schemas for the same
    company: a ticker rebuilt from XBRL came back with free_cash_flow
    5/109 against a CSV that had it everywhere, and export_csv.py
    correctly refused to blank 488 populated cells.

    Every input is already scaled to millions by the caller. A missing
    input leaves the output NULL -- never a zero, which would read as a
    real measurement of no cash flow.
    """
    ocf, capex = rec.get("operating_cash_flow"), rec.get("capex")
    if rec.get("free_cash_flow") is None and ocf is not None and capex is not None:
        # SEC tags PaymentsToAcquire... as a positive outflow.
        rec["free_cash_flow"] = ocf - abs(capex)

    # EBITDA is a convention too, and its D&A input is a kpi rather than a
    # core column, so the caller passes it in. Without this the column came
    # back 0/106 on ADBE, leaving the dashboard and the Step 8c EV/EBITDA
    # check nothing to work with on a filer that had both inputs for 56
    # periods.
    oi = rec.get("operating_income")
    if rec.get("ebitda") is None and oi is not None and da is not None:
        rec["ebitda"] = oi + da

    revenue = rec.get("revenue")
    if revenue:
        for margin, part in (("gross_margin", "gross_profit"),
                             ("operating_margin", "operating_income"),
                             ("net_margin", "net_income")):
            if rec.get(margin) is None and rec.get(part) is not None:
                rec[margin] = rec[part] / revenue * 100


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="report coverage only; write nothing")
    args = ap.parse_args()
    ticker = args.ticker

    cik = cik_for(ticker)
    if cik is None:
        print(f"{ticker}: not a US filer in SEC's map -- use build_facts.py",
              file=sys.stderr)
        return 2

    cache = CACHE / f"CIK{cik:010d}.json"
    try:
        facts = (json.loads(cache.read_text()) if cache.exists()
                 else fetch(FACTS_URL.format(cik=cik), cache))
    except Exception as e:
        print(f"{ticker}: SEC fetch failed: {e}", file=sys.stderr)
        return 1

    rows: dict[str, dict[str, Any]]
    kpis: list[tuple[str, str, float, str]]
    missing: list[str]
    used: dict[str, str | None]
    rows, kpis, missing, used = {}, [], [], {}
    resolved: list[tuple[str, str | None, dict[str, Any]]] = []
    for col, concepts in CONCEPTS.items():
        concept, values, _unit = collect(facts, concepts)
        resolved.append((col, concept, values))
    for col, components in SUM_CONCEPTS.items():
        concept, values, _unit = collect_sum(facts, components)
        resolved.append((col, concept, values))

    for col, concept, values in resolved:
        if not values:
            missing.append(col)
            continue
        used[col] = concept
        for period, val in values.items():
            if col.startswith("_"):
                # Same scaling as core_metrics: these are money amounts
                # (D&A, buybacks, interest income), reported in millions.
                kpis.append((period, col.lstrip("_"), float(val) / 1e6, "millions"))
            else:
                rows.setdefault(period, {})[col] = float(val)

    print(f"{ticker}: CIK {cik}, {facts.get('entityName','?')}")
    print(f"  {len(rows)} periods, {len(used)} concepts matched, "
          f"{len(missing)} missing")
    if args.check:
        if missing:
            print(f"  missing: {', '.join(missing)}")
        return 0

    import duckdb
    db = REPO / "research" / ticker / "Reports" / f"{ticker}.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    schema.ensure_schema(con)
    con.execute("DELETE FROM core_metrics")
    con.execute("DELETE FROM kpis")

    cols = schema.CORE_NAMES
    payload: list[list[Any]] = []
    # XBRL reports absolute dollars; the text path and every existing CSV
    # use millions. Scale here so core_metrics has ONE convention -- the
    # alternative left export_csv.py writing 45183036000 into a CSV whose
    # dashboard embeds 45183.04, silently breaking it by 1e6.
    #
    # Per-share and percentage columns are scale-free and must not be
    # touched: EPS of 1.98 is 1.98 whatever the revenue units are.
    scale_free = {"period", "units", "currency", "eps", "dividend_per_share",
                  "gross_margin", "operating_margin", "net_margin"}
    scaled: dict[str, dict[str, Any]] = {}
    for period, vals in rows.items():
        rec: dict[str, Any] = dict.fromkeys(cols)
        rec["period"] = period
        rec["units"] = "millions"
        rec["currency"] = "USD"
        for k, v in vals.items():
            if k not in rec:
                continue
            rec[k] = v / 1e6 if (k not in scale_free and v is not None) else v
        scaled[period] = rec

    # After scaling (so the subtraction is in one unit) and before derive(),
    # which turns the decumulated OCF and capex into a Q2/Q3 free_cash_flow.
    decumulate(scaled)
    drop_scaffolding(scaled)

    # D&A is a kpi, not a core column, but EBITDA needs it.
    da_by_period = {p: v for p, name, v, _u in kpis if name == "ebitda_da"}

    for period, rec in sorted(scaled.items()):
        # decumulate() can introduce a quarter that had no facts of its own,
        # so fill the schema shape before indexing by column.
        for col in cols:
            rec.setdefault(col, None)
        rec["period"] = period
        rec["units"] = "millions"
        rec["currency"] = "USD"
        derive(rec, da=da_by_period.get(period))
        payload.append([rec[c] for c in cols])
    if payload:
        con.executemany(
            f"INSERT INTO core_metrics ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})", payload)
    if kpis:
        con.executemany("INSERT INTO kpis VALUES (?,?,?,?)", kpis)
    con.close()

    print(f"  -> {db.name}")
    if args.show:
        for col, concept in sorted(used.items()):
            print(f"    {col:24s} <- {concept}")
    if missing:
        print(f"  NOT TAGGED (left NULL): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
