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
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import schema  # noqa: E402

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
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
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


def fetch(url, dest=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if dest:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return json.loads(data)


def cik_for(ticker):
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


def period_label(fact):
    """Derive the period from the fact's own dates, not its `fy`.

    A 10-K restates prior years, so `fy` is the filing's year rather than
    the fact's. start/end are authoritative.
    """
    end = fact.get("end", "")
    start = fact.get("start")
    if not end:
        return None
    year = end[:4]
    if not start:                       # instant (balance sheet)
        return f"FY{year}"
    days = (_d(end) - _d(start)).days
    if days > 300:
        return f"FY{year}"
    if days > 150:
        half = "H1" if int(end[5:7]) <= 8 else "H2"
        return f"{half}-{year}"
    q = (int(end[5:7]) - 1) // 3 + 1
    return f"Q{q} {year}"


def _d(s):
    import datetime as dt
    return dt.date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def collect(facts, concepts):
    """Best value per period, merged across every listed concept.

    Filers switch tags over time -- PayPal reports Revenues for 2018-2025
    and RevenueFromContractWithCustomer... only for 2016-2019. Taking the
    first concept that has *any* data would silently drop half the
    history, so merge them and let concept order break ties within a
    period.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = {}
    used = []
    unit_seen = None
    for pref, concept in enumerate(concepts):
        entry = gaap.get(concept)
        if not entry:
            continue
        got = False
        for unit, rows in entry.get("units", {}).items():
            unit_seen = unit_seen or unit
            for f in rows:
                p = period_label(f)
                if not p:
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
        if got:
            used.append(concept)
    return ("+".join(used) if used else None,
            {p: v[0] for p, v in out.items()},
            unit_seen or "USD")


def main():
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

    rows, kpis, missing, used = {}, [], [], {}
    for col, concepts in CONCEPTS.items():
        concept, values, unit = collect(facts, concepts)
        if not values:
            missing.append(col)
            continue
        used[col] = concept
        for period, val in values.items():
            if col.startswith("_"):
                kpis.append((period, col.lstrip("_"), float(val), unit))
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
    con.execute(schema.create_sql())
    con.execute("DELETE FROM core_metrics")
    con.execute("DELETE FROM kpis")

    cols = schema.CORE_NAMES
    payload = []
    for period, vals in sorted(rows.items()):
        rec = {c: None for c in cols}
        rec["period"] = period
        rec["units"] = "absolute"       # XBRL values are unscaled
        rec["currency"] = "USD"
        rec.update({k: v for k, v in vals.items() if k in rec})
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
