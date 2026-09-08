#!/usr/bin/env python3
"""Step 8c of the research-stock skill: implied multiples vs. ten years of history.

A DCF can produce an intrinsic value implying multiples the market has never
paid. SEK.NZ is the canonical failure: a peak-FCF model produced IV $22.75,
implying 30x P/E and 3.4x P/B on a company that has never traded above 0.99x
P/B or 7.8x EV/EBITDA in a decade.

The check was written into SKILL.md as seven prose steps and performed by
hand by the research orchestrator (Opus, $1.54/run, 27 turns, 18% of a
ticker's cost). Six of the seven are arithmetic: fetch the FY-end closes,
align them to the annual rows, divide, compare against four thresholds,
name the suspects. Only 8c.5 -- deciding WHICH assumption to change -- is
judgment, and that stays with the dcf-analyst.

    sanity_check.py SEK.NZ              # print the block, write nothing
    sanity_check.py SEK.NZ --apply      # write it into the DCF JSON
    sanity_check.py --all --dry         # corpus diff: stored vs computed

Exit codes: 0 clean (or a model whose checks do not apply), 2 refused
(no DCF, or a price denominated unlike the financials), 3 tripped. Exit 3
is the orchestrator's signal to spawn the dcf-analyst ONCE with the printed
trip reasons.

TWO THINGS THIS DELIBERATELY DOES NOT DO:

  * It never adjusts the DCF. Naming the suspect is arithmetic; choosing
    between mid-cycling the FCF, raising the WACC and capping the exit
    multiple depends on why the business is the way it is, and a script
    that guessed would produce an auditable-looking record of a decision
    nobody made.

  * It never mixes an SBC-adjusted multiple with an unadjusted one. Where
    `stock_based_comp` is NULL for a year, that year's earnings multiples
    are None -- not computed unadjusted. Comparing an adjusted implied
    multiple against a half-unadjusted historical average manufactures a
    trip, or hides one.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import re
import statistics
import sys
import time

import periods
import quotes
from schema import normalize

REPO = pathlib.Path(__file__).resolve().parents[1]

COMPUTED_BY = "scripts/sanity_check.py"

# The multiples are computed on SBC-adjusted earnings on both sides, so
# they deliberately will NOT match Yahoo's or a screener's GAAP P/E. Said
# in the output so a future reader does not "correct" it back.
BASIS = ("SBC-adjusted on both sides (EPS less SBC/share; EBITDA less SBC), "
         "so these deliberately do not match GAAP screener multiples.")

# 8c.3. Each is a ratio against history, not an absolute level: a 40x P/E
# is fine for a business that has always traded at 35x.
PE_MULTIPLE_OF_AVG = 2.0
PB_MULTIPLE_OF_MAX = 1.5
EV_EBITDA_MULTIPLE_OF_MAX = 1.5
UPSIDE_TRIP_PCT = 100.0
PRICE_POSITION_TRIP = 0.5

# 8c.4.1 -- last_fcf above this multiple of the historical median is a peak.
PEAK_FCF_MULTIPLE = 1.5
# 8c.4.4 -- long-run GDP growth. Above this for a mature business is a flag.
TERMINAL_GROWTH_CEILING = 0.03

# Re-fetch the monthly series only when the newest cached row has aged past
# a month. Monthly bars do not change once closed, so this is a cache with
# an obvious invalidation rule rather than a guess about freshness.
CACHE_MAX_AGE_DAYS = 35

# 8c.1's fallback when a month has no close (halted, pre-listing, a data
# gap). One quarter back at most: an anchor from a year earlier is not the
# FY-end price by any reading.
MAX_ANCHOR_BACKFILL_DAYS = 100

# Money in different units is still a mismatch: GBP and GBp differ by a
# factor of 100, which is exactly the error this guard exists to catch, so
# they must NOT normalise together. Only genuinely identical spellings
# alias -- GBX is the other way of writing pence -- and the comparison is
# otherwise case-sensitive on purpose.
CURRENCY_ALIASES = {"GBX": "GBp", "gbx": "GBp"}

# 8c.4.2 -- structural risks a standard WACC does not price. Matched against
# the qualitative analysis's own risk text.
STRUCTURAL_RISK_KEYWORDS = {
    "customer concentration": (r"single[- ]customer|customer concentration|"
                               r"one customer|single[- ]desk|sole (?:buyer|customer)|"
                               r"dependency on \w+ (?:for|to)"),
    "illiquidity": r"illiquid|thinly traded|small[- ]cap|micro[- ]cap|low free float",
    "cyclicality": r"cyclical|extreme volatility|boom[- ]bust|commodity price",
    "regulatory single point of failure": (r"regulatory|licen[cs]e|permit|"
                                           r"single[- ]point|concession|quota"),
    "geographic concentration": r"single (?:country|region|market)|geographic concentration",
}

# The six non-owner-FCF model families from dcf-methods/SKILL.md. A REIT has
# no meaningful EV/EBITDA and an LIC has no earnings at all, so grading them
# against P/E and EV/EBITDA thresholds would produce a confident verdict
# about arithmetic that was never performed.
MODEL_PATTERNS: list[tuple[str, str]] = [
    ("affo", r"\baffo\b|funds from operations|distributable profit"),
    ("risked_npv", r"risked[- ]?(?:project[- ]?)?npv|pre[- ]revenue|resource ounces"),
    ("asset_waterfall", (r"waterfall|asset[- _]scenario|shell value|"
                         r"receivership|liquidation|creditor")),
    ("book_value", (r"\bbvps\b|book[- _]value compounding|residual income|"
                    r"price[- ]to[- ]book|\bp/b\b compounding")),
    ("nav", r"\bnav\b|\bnta\b|net asset value|net tangible asset"),
    ("earnings_at_coe", (r"cost[- _]of[- _]equity|distributable[- _]earnings|"
                         r"dividend[- _]discount|earnings[- _]based")),
    ("owner_fcf", (r"owner[- _]?fcf|free cash flow|\bfcf\b|"
                   r"discounted cash flow|\bdcf\b")),
]

# Where a DCF states its model. Free text in every one of them -- the corpus
# has 30-odd distinct spellings -- so it is matched by keyword, not equality.
MODEL_FIELDS = ("valuation_model", "model", "model_type", "method",
                "methodology")

METRIC_COLUMNS = ["revenue", "ebitda", "net_income", "shareholders_equity",
                  "total_debt", "cash_and_equivalents", "shares_outstanding",
                  "stock_based_comp", "free_cash_flow"]


class DenominationError(Exception):
    """The price is not denominated like the financials it would divide."""


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def _reports(repo: pathlib.Path, ticker: str) -> pathlib.Path:
    return pathlib.Path(repo) / "research" / ticker / "Reports"


def _read_cache(path: pathlib.Path) -> list[tuple[dt.date, float]]:
    if not path.exists():
        return []
    rows: list[tuple[dt.date, float]] = []
    for row in csv.DictReader(path.read_text().splitlines()):
        try:
            rows.append((dt.date.fromisoformat((row.get("Date") or "").strip()),
                         float(row["Close"])))
        except (TypeError, ValueError, KeyError):
            continue
    rows.sort(key=lambda r: r[0])
    return rows


def _write_cache(path: pathlib.Path, rows: list[tuple[dt.date, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["Date,Close"] + [f"{d.isoformat()},{c}" for d, c in rows]
    path.write_text("\n".join(lines) + "\n")


def price_series(repo: pathlib.Path, ticker: str,
                 today: dt.date | None = None) -> list[tuple[dt.date, float]]:
    """Ten years of monthly closes, cached beside the metrics.

    The cache is committed: 120 rows of Date,Close is ~2KB and it makes the
    whole check reproducible offline, and reviewable -- a wrong FY anchor
    shows up in the diff rather than in a fetch nobody can replay.

    A failed re-fetch keeps the stale cache. An empty series would compute
    every multiple as None and report a clean pass, which is a worse
    failure than slightly old prices.
    """
    path = _reports(repo, ticker) / f"{ticker}_Prices.csv"
    cached = _read_cache(path)
    now = today or dt.date.today()
    if cached and (now - cached[-1][0]).days <= CACHE_MAX_AGE_DAYS:
        return cached
    fetched = quotes.monthly_for_ticker(ticker, pathlib.Path(repo) / "research")
    if not fetched:
        return cached
    _write_cache(path, fetched)
    return fetched


def fy_end_month(repo: pathlib.Path, ticker: str) -> tuple[int, str]:
    """(month, where it came from). December is the last resort, and says so."""
    base = pathlib.Path(repo) / "research" / ticker
    info_path = base / "info.json"
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text()).get("fiscal_year_end")
        except (OSError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, str):
            m = re.match(r"^\s*(\d{1,2})\s*-\s*\d{1,2}\s*$", raw)
            if m and 1 <= int(m.group(1)) <= 12:
                return int(m.group(1)), "info.json:fiscal_year_end"

    extracted = base / "Extracted"
    if extracted.is_dir():
        try:
            import build_facts
            month = build_facts.folder_fiscal_year_end(
                sorted(extracted.glob("*.txt")))
        except Exception:
            month = None
        if month:
            return month, "learned from the annual filings"

    return 12, "December (fallback: no fiscal_year_end and no annual filing)"


def _db_annual_rows(db: pathlib.Path) -> list[dict[str, object]]:
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = ["period", *METRIC_COLUMNS]
        got = con.execute(
            f"select {', '.join(cols)} from metrics_normalized").fetchall()
    finally:
        con.close()
    return [dict(zip(cols, r, strict=True)) for r in got]


def _csv_annual_rows(path: pathlib.Path) -> list[dict[str, object]]:
    """The CSV is the fallback when the gitignored DB has not been rebuilt.

    It is already in the canonical scale (export_csv writes what the
    normalized view produced), so there is no rescaling to redo here.
    """
    rows: list[dict[str, object]] = []
    for raw in csv.DictReader(path.read_text().splitlines()):
        row: dict[str, object] = {"period": (raw.get("Period") or "").strip()}
        for header, value in raw.items():
            column = normalize(header)
            if column is None or column not in METRIC_COLUMNS:
                continue
            try:
                row[column] = float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                row[column] = None
        rows.append(row)
    return rows


def annual_rows(repo: pathlib.Path, ticker: str) -> list[dict[str, object]]:
    """Genuine 12-month years only, oldest first, in millions.

    `periods.is_annual` is what decides: FY2017-15mo and FY2018-6moStub are
    FY-shaped and are not years, and `startswith("FY")` gets both wrong.
    """
    reports = _reports(repo, ticker)
    db = reports / f"{ticker}.duckdb"
    csv_path = reports / f"{ticker}_Metrics.csv"
    if db.exists():
        rows = _db_annual_rows(db)
    elif csv_path.exists():
        rows = _csv_annual_rows(csv_path)
    else:
        return []
    annual = [r for r in rows
              if periods.is_annual(periods.parse(str(r.get("period") or "")))]
    annual.sort(key=lambda r: periods.sort_key(str(r.get("period") or "")))
    return annual


# --------------------------------------------------------------------------
# 8c.1 -- historical multiples
# --------------------------------------------------------------------------

def fy_end_price(series: list[tuple[dt.date, float]], year: int,
                 month: int) -> float | None:
    """The close at the fiscal year end, or the nearest earlier one.

    Never a later close: a price the market had not yet set cannot value
    that year. And never more than a quarter back, or the "FY2024 anchor"
    is really the FY2023 one.
    """
    target = dt.date(year, month, 1)
    best: tuple[dt.date, float] | None = None
    for when, close in series:
        if when.year == year and when.month == month:
            return close
        if when <= target and (best is None or when > best[0]):
            best = (when, close)
    if best is None or (target - best[0]).days > MAX_ANCHOR_BACKFILL_DAYS:
        return None
    return best[1]


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def multiples(price: float | None, row: dict[str, object]) -> dict[str, float | None]:
    """The four multiples for one year, on the SBC-adjusted basis.

    `stock_based_comp` NULL yields None for the earnings multiples rather
    than an unadjusted number. P/B and P/Sales never touch earnings, so SBC
    cannot affect them and they survive.
    """
    shares = _num(row.get("shares_outstanding"))
    equity = _num(row.get("shareholders_equity"))
    revenue = _num(row.get("revenue"))
    ebitda = _num(row.get("ebitda"))
    net_income = _num(row.get("net_income"))
    sbc = _num(row.get("stock_based_comp"))
    debt = _num(row.get("total_debt"))
    cash = _num(row.get("cash_and_equivalents"))

    market_cap = price * shares if price is not None and shares is not None else None
    net_debt = debt - cash if debt is not None and cash is not None else None

    pe: float | None = None
    if price is not None and sbc is not None and net_income is not None \
            and shares is not None and shares > 0:
        adjusted_eps = (net_income - sbc) / shares
        if adjusted_eps > 0:
            pe = price / adjusted_eps

    ev_ebitda: float | None = None
    if market_cap is not None and net_debt is not None and sbc is not None \
            and ebitda is not None:
        adjusted_ebitda = ebitda - sbc
        if adjusted_ebitda > 0:
            ev_ebitda = (market_cap + net_debt) / adjusted_ebitda

    return {"pe": pe, "pb": _div(market_cap, equity), "ev_ebitda": ev_ebitda,
            "p_sales": _div(market_cap, revenue)}


def historical_table(rows: list[dict[str, object]],
                     series: list[tuple[dt.date, float]], month: int,
                     fx_rate: float | None = None) -> list[dict[str, object]]:
    """One entry per FY: the anchor price and the four multiples."""
    table: list[dict[str, object]] = []
    for row in rows:
        period = str(row.get("period") or "")
        parsed = periods.parse(period)
        year = parsed.fiscal_year
        price = fy_end_price(series, year, month) if year is not None else None
        if price is not None and fx_rate is not None:
            price *= fx_rate
        entry: dict[str, object] = {"period": period,
                                    "price": round(price, 6) if price is not None else None}
        for key, value in multiples(price, row).items():
            entry[key] = round(value, 4) if value is not None else None
        table.append(entry)
    return table


def averages(table: list[dict[str, object]]) -> dict[str, float | None]:
    """Means and maxima, skipping nulls. An all-null column is None, not 0.

    "ex outliers" in the key name is the spec's: excluding a cyclone or a
    COVID write-down year is a judgment about that company, so the year
    stays in `historical_table` for the agent to see and exclude.
    """
    out: dict[str, float | None] = {}
    for key in ("pe", "pb", "ev_ebitda", "p_sales"):
        values = [v for v in (_num(r.get(key)) for r in table) if v is not None]
        out[f"{key}_avg"] = round(statistics.fmean(values), 4) if values else None
        out[f"{key}_max"] = round(max(values), 4) if values else None
        out[f"{key}_min"] = round(min(values), 4) if values else None
    return out


# --------------------------------------------------------------------------
# 8c.2 / 8c.3
# --------------------------------------------------------------------------

def implied_multiples(intrinsic_value: float,
                      latest: dict[str, object]) -> dict[str, float | None]:
    """What the base IV would imply on the latest FY actuals -- 8c.2.

    Identical arithmetic to the historical row, with IV standing in for the
    market price. That is the point: the comparison is only meaningful if
    both sides are computed the same way.
    """
    return {k: round(v, 4) if v is not None else None
            for k, v in multiples(intrinsic_value, latest).items()}


def price_position(price: float, series: list[tuple[dt.date, float]]) -> float | None:
    """Where the current price sits in the 10y range, 0 (low) to 1 (high)."""
    if not series:
        return None
    closes = [c for _, c in series]
    low, high = min(closes), max(closes)
    if high == low:
        return 0.5
    return (price - low) / (high - low)


def trip_reasons(implied: dict[str, float | None],
                 historical: dict[str, float | None],
                 upside_pct: float | None,
                 position: float | None) -> list[str]:
    """8c.3. A missing number never trips -- absence is not evidence."""
    reasons: list[str] = []

    pe, pe_avg = implied.get("pe"), historical.get("pe_avg")
    if pe is not None and pe_avg is not None and pe > PE_MULTIPLE_OF_AVG * pe_avg:
        reasons.append(f"Implied P/E {pe:.1f}x exceeds {PE_MULTIPLE_OF_AVG:g}x "
                       f"the 10yr average {pe_avg:.1f}x")

    pb, pb_max = implied.get("pb"), historical.get("pb_max")
    if pb is not None and pb_max is not None and pb > PB_MULTIPLE_OF_MAX * pb_max:
        reasons.append(f"Implied P/B {pb:.2f}x exceeds {PB_MULTIPLE_OF_MAX:g}x "
                       f"the 10yr maximum {pb_max:.2f}x")

    ev, ev_max = implied.get("ev_ebitda"), historical.get("ev_ebitda_max")
    if ev is not None and ev_max is not None \
            and ev > EV_EBITDA_MULTIPLE_OF_MAX * ev_max:
        reasons.append(f"Implied EV/EBITDA {ev:.1f}x exceeds "
                       f"{EV_EBITDA_MULTIPLE_OF_MAX:g}x the 10yr maximum {ev_max:.1f}x")

    # Both halves must hold. Big upside on a beaten-down price is the thing
    # the screener is for; big upside on a price already near its decade
    # high is the market disagreeing with the model about good news it can
    # already see.
    if upside_pct is not None and position is not None \
            and upside_pct > UPSIDE_TRIP_PCT and position > PRICE_POSITION_TRIP:
        reasons.append(f"Base case upside {upside_pct:.0f}% with price in the "
                       f"upper half of the 10yr range ({position:.0%})")
    return reasons


# --------------------------------------------------------------------------
# 8c.4 -- diagnosis candidates
# --------------------------------------------------------------------------

def _risk_texts(risks: object) -> list[str]:
    """Analysis.json stores risks as a list or as a dict of lists by theme."""
    out: list[str] = []
    if isinstance(risks, dict):
        for value in risks.values():
            out.extend(_risk_texts(value))
    elif isinstance(risks, list):
        for item in risks:
            out.extend(_risk_texts(item))
    elif isinstance(risks, str):
        out.append(risks)
    if isinstance(risks, dict):
        out.extend(risks[key] for key in ("risk", "description", "title")
                   if isinstance(risks.get(key), str))
    return out


def diagnose(inputs: dict[str, object], fcf_history: list[float],
             risks: object, growth_pct: float | None,
             terminal_growth: float | None,
             revenue_cagr_pct: float | None = None) -> list[dict[str, object]]:
    """Candidate causes, in the spec's priority order.

    Candidates, not a verdict: each says what was measured and leaves the
    conclusion to the dcf-analyst. Peak-FCF is first because it is the most
    common, and it was SEK.NZ's.
    """
    out: list[dict[str, object]] = []

    last_fcf = _num(inputs.get("last_fcf"))
    clean = [v for v in fcf_history if v is not None]
    if last_fcf is not None and len(clean) >= 3:
        median = statistics.median(clean)
        if median > 0 and last_fcf > PEAK_FCF_MULTIPLE * median:
            out.append({
                "candidate": "peak_fcf",
                "detail": (f"Base year FCF {last_fcf:,.1f} is "
                           f"{last_fcf / median:.1f}x the historical median {median:,.1f}"),
                "last_fcf": round(last_fcf, 4),
                "median": round(median, 4),
                "ratio": round(last_fcf / median, 3),
            })

    texts = " ".join(_risk_texts(risks)).lower()
    matched = [name for name, pattern in STRUCTURAL_RISK_KEYWORDS.items()
               if re.search(pattern, texts)]
    if matched:
        out.append({
            "candidate": "wacc_too_low",
            "detail": ("Structural risks in the qualitative analysis that a "
                       "standard WACC may not price: " + ", ".join(matched)),
            "keywords": matched,
        })

    if growth_pct is not None and revenue_cagr_pct is not None \
            and growth_pct > revenue_cagr_pct:
        out.append({
            "candidate": "growth_too_high",
            "detail": (f"Projected growth {growth_pct:.1f}% exceeds the "
                       f"through-cycle revenue CAGR {revenue_cagr_pct:.1f}%"),
            "projected_pct": round(growth_pct, 3),
            "through_cycle_cagr_pct": round(revenue_cagr_pct, 3),
        })

    if terminal_growth is not None:
        # The corpus stores it both ways: 0.025 and 2.5 both mean 2.5%.
        rate = terminal_growth / 100 if terminal_growth > 0.5 else terminal_growth
        if rate > TERMINAL_GROWTH_CEILING:
            out.append({
                "candidate": "terminal_growth_too_high",
                "detail": (f"Terminal growth {rate:.1%} is above long-run GDP "
                           f"({TERMINAL_GROWTH_CEILING:.0%}); cyclicals should "
                           "sit at 1.5-2.5%"),
                "terminal_growth": round(rate, 5),
            })
    return out


# --------------------------------------------------------------------------
# Model routing
# --------------------------------------------------------------------------

def dcf_model_label(doc: dict[str, object]) -> str:
    """Which valuation family this DCF used.

    The corpus states it in five different fields and thirty-odd spellings,
    so it is keyword-matched, never compared for equality. An unlabelled
    model with `inputs.last_fcf` is an owner-FCF DCF -- that field only
    exists in one.
    """
    haystack: list[str] = []
    for field in MODEL_FIELDS:
        value = doc.get(field)
        if isinstance(value, str):
            haystack.append(value)
    philosophy = doc.get("valuation_philosophy")
    if isinstance(philosophy, dict):
        for field in ("model", "method", "approach", "methodology"):
            value = philosophy.get(field)
            if isinstance(value, str):
                haystack.append(value)
    text = " ".join(haystack).lower()
    for name, pattern in MODEL_PATTERNS:
        if re.search(pattern, text):
            return name
    inputs = doc.get("inputs")
    if isinstance(inputs, dict) and _num(inputs.get("last_fcf")) is not None:
        return "owner_fcf"
    return "unknown"


def _canon_currency(code: object) -> str | None:
    if not isinstance(code, str) or not code.strip():
        return None
    text = code.strip()
    return CURRENCY_ALIASES.get(text, text)


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

def _load_json(path: pathlib.Path) -> dict[str, object]:
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def check(repo: pathlib.Path, ticker: str,
          today: dt.date | None = None) -> dict[str, object]:
    """The 8c.7 block for one ticker. Raises DenominationError on a
    price/financials currency mismatch with no fx_rate to bridge it."""
    repo = pathlib.Path(repo)
    reports = _reports(repo, ticker)
    dcf_path = reports / f"{ticker}_DCF.json"
    if not dcf_path.exists():
        raise FileNotFoundError(dcf_path)
    doc = _load_json(dcf_path)
    raw_inputs = doc.get("inputs")
    inputs: dict[str, object] = raw_inputs if isinstance(raw_inputs, dict) else {}

    reporting = _canon_currency(inputs.get("currency")) or _canon_currency(doc.get("currency"))
    quoted = _canon_currency(inputs.get("quote_currency"))
    fx_rate = _num(inputs.get("fx_rate"))
    if reporting and quoted and reporting != quoted and fx_rate is None:
        raise DenominationError(
            f"{ticker}: quote is {quoted} but the financials are {reporting}, "
            "and inputs.fx_rate is absent. WISE.L's 885.6 GBp over 48.43 USD "
            "yields a plausible P/E of 18.3 that is pure coincidence.")
    convert = fx_rate if (reporting and quoted and reporting != quoted) else None

    month, fy_source = fy_end_month(repo, ticker)
    series = price_series(repo, ticker, today=today)
    rows = annual_rows(repo, ticker)
    table = historical_table(rows, series, month, convert)
    hist = averages(table)

    model = dcf_model_label(doc)
    base = doc.get("valuation")
    base_case = base.get("base") if isinstance(base, dict) else None
    intrinsic = _num(base_case.get("intrinsic_value")) if isinstance(base_case, dict) else None
    upside = _num(base_case.get("upside")) if isinstance(base_case, dict) else None
    price = _num(doc.get("current_price"))
    if price is not None and convert is not None:
        price *= convert
    position = price_position(price, series) if price is not None else None

    latest = rows[-1] if rows else {}
    implied = (implied_multiples(intrinsic, latest)
               if intrinsic is not None and latest else
               {"pe": None, "pb": None, "ev_ebitda": None, "p_sales": None})

    block: dict[str, object] = {
        "ran": True,
        "passed": True,
        "model": model,
        "basis": BASIS,
        "fy_end_month": month,
        "fy_end_month_source": fy_source,
        "implied_multiples": implied,
        "historical_averages_ex_outliers": hist,
        "price_position_in_10yr_range": (round(position, 4)
                                         if position is not None else None),
        "trip_reasons": [],
        "diagnosis": [],
        "historical_table": table,
        "computed_by": COMPUTED_BY,
        "computed_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
    }

    if model not in ("owner_fcf", "unknown"):
        # A REIT's AFFO capitalization and an LIC's NAV model do not produce
        # an earnings multiple to grade. A `false` here would be a verdict
        # about arithmetic that was never performed.
        block["passed"] = None
        block["checks_replaced"] = (
            f"model is {model}: P/E and EV/EBITDA trip rules do not apply; "
            "the historical table is reported for context only")
        return block

    reasons = trip_reasons(implied, hist, upside, position)
    block["trip_reasons"] = reasons
    block["passed"] = not reasons
    if reasons:
        fcf_history = [v for v in (_num(r.get("free_cash_flow")) for r in rows)
                       if v is not None]
        analysis = _load_json(reports / f"{ticker}_Analysis.json")
        growth = doc.get("historical_growth")
        growth_pct = (_num(growth.get("selected_growth_rate"))
                      if isinstance(growth, dict) else None)
        cagr_pct = _revenue_cagr_pct(rows)
        assumptions = doc.get("assumptions")
        terminal = (_num(assumptions.get("terminal_growth"))
                    if isinstance(assumptions, dict) else None)
        block["diagnosis"] = diagnose(inputs, fcf_history,
                                      analysis.get("risks"), growth_pct,
                                      terminal, cagr_pct)
    return block


def _revenue_cagr_pct(rows: list[dict[str, object]]) -> float | None:
    """Through-cycle revenue CAGR, first genuine year to last -- not the
    cyclical recovery window, which is what 8c.4.3 warns about."""
    values = {str(r.get("period") or ""): v
              for r in rows if (v := _num(r.get("revenue"))) is not None}
    if len(values) < 2:
        return None
    import fundamentals
    reasons: list[str] = []
    span = len(values) - 1
    cagr, _ = fundamentals._cagr(values, span, reasons, "revenue")
    return cagr * 100 if cagr is not None else None


# --------------------------------------------------------------------------
# Writing it back
# --------------------------------------------------------------------------

def _block_end(text: str, start: int) -> int:
    """Index just past the `{...}` opening at/after `start`."""
    i = start
    while i < len(text) and text[i] != "{":
        if text[i] not in " \t\r\n:":
            return -1
        i += 1
    if i >= len(text):
        return -1
    depth, in_str, esc = 0, False, False
    for j in range(i, len(text)):
        ch = text[j]
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
    return -1


def rewrite_sanity_check(original: str, block: dict[str, object]) -> str:
    """Replace only the `sanity_check` object, byte-for-byte elsewhere.

    `json.dumps(indent=2)` on the whole document re-flows every inline
    array in the file -- a DCF carrying `"fcf_growth_rates": [22.0, 20.0,
    17.0]` gets exploded onto five lines -- which turns a one-key update
    into a 240-line diff and buries the real change. This artifact is
    reviewed by hand, so the edit is surgical.
    """
    body = json.dumps(block, indent=2)
    indented = "\n".join(("  " + ln if ln.strip() else ln)
                         for ln in body.splitlines()).lstrip()

    m = re.search(r'"sanity_check"\s*:\s*', original)
    if m:
        end = _block_end(original, m.end())
        if end != -1:
            return original[:m.end()] + indented + original[end:]

    # Not present: append it as a new top-level key.
    stripped = original.rstrip()
    close = stripped.rfind("}")
    if close == -1:
        return json.dumps({**json.loads(original), "sanity_check": block},
                          indent=2) + "\n"
    head = stripped[:close].rstrip()
    sep = "" if head.endswith(("{", ",")) else ","
    return f'{head}{sep}\n  "sanity_check": {indented}\n}}\n'


def apply(repo: pathlib.Path, ticker: str,
          today: dt.date | None = None) -> dict[str, object]:
    """Compute the block and write it into the DCF, preserving the agent's
    own record of the fix it applied under 8c.5."""
    path = _reports(repo, ticker) / f"{ticker}_DCF.json"
    block = check(repo, ticker, today=today)
    original = path.read_text()
    existing = _load_json(path).get("sanity_check")
    if isinstance(existing, dict):
        # 8c.5 is the dcf-analyst's work. Overwriting its record of what it
        # changed, and the multiples it got afterwards, would make the fix
        # unauditable on the next run.
        for key in ("fix_applied", "implied_multiples_after_fix"):
            if key in existing:
                block[key] = existing[key]
    text = rewrite_sanity_check(original, block)
    try:
        json.loads(text)
    except json.JSONDecodeError:
        doc = _load_json(path)
        doc["sanity_check"] = block
        text = json.dumps(doc, indent=2) + "\n"
    path.write_text(text)
    return block


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _researched(repo: pathlib.Path) -> list[str]:
    root = pathlib.Path(repo) / "research"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if (p / "Reports" / f"{p.name}_DCF.json").exists())


def _stored_passed(repo: pathlib.Path, ticker: str) -> object:
    doc = _load_json(_reports(repo, ticker) / f"{ticker}_DCF.json")
    block = doc.get("sanity_check")
    if not isinstance(block, dict):
        return "none"
    passed = block.get("passed")
    if passed is None and isinstance(block.get("status"), str):
        return block["status"].strip().upper() in ("PASSED", "PASS", "OK")
    return passed if passed is not None else "none"


def _sweep(repo: pathlib.Path, delay: float,
           today: dt.date | None = None) -> int:
    """--all --dry: the corpus diff. Writes nothing but the price cache."""
    print(f"{'ticker':14s} {'stored':8s} {'computed':9s} {'model':16s} reasons")
    print("-" * 100)
    for i, ticker in enumerate(_researched(repo)):
        if i and delay:
            time.sleep(delay)
        try:
            block = check(repo, ticker, today=today)
        except DenominationError as e:
            print(f"{ticker:14s} {'-':8s} {'REFUSED':9s} {'-':16s} {e}")
            continue
        except Exception as e:
            print(f"{ticker:14s} {'-':8s} {'ERROR':9s} {'-':16s} "
                  f"{type(e).__name__}: {str(e)[:60]}")
            continue
        stored = _stored_passed(repo, ticker)
        computed = block["passed"]
        flag = "DIFF " if (stored is not computed and stored != "none") else ""
        tripped = block["trip_reasons"]
        reasons = ("; ".join(str(r) for r in tripped)
                   if isinstance(tripped, list) else "")
        print(f"{ticker:14s} {stored!s:8s} {computed!s:9s} "
              f"{block['model']!s:16s} {flag}{reasons[:70]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("ticker", nargs="?", default="")
    p.add_argument("--apply", action="store_true",
                   help="write the block into the DCF JSON")
    p.add_argument("--all", action="store_true", help="every ticker with a DCF")
    p.add_argument("--dry", action="store_true", help="write nothing (with --all)")
    p.add_argument("--delay", type=float, default=1.0,
                   help="seconds between tickers (Yahoo rate-limits)")
    p.add_argument("--as-of", default="",
                   help="treat this ISO date as today (price-cache freshness)")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        today = dt.date.fromisoformat(args.as_of) if args.as_of else None
    except ValueError:
        print(f"refused: --as-of {args.as_of!r} is not an ISO date",
              file=sys.stderr)
        return 2

    if args.all:
        return _sweep(REPO, args.delay, today=today)
    if not args.ticker:
        p.print_usage(sys.stderr)
        print("pass a TICKER, or --all --dry", file=sys.stderr)
        return 2

    ticker = args.ticker.upper()
    try:
        block = (apply(REPO, ticker, today=today) if args.apply
                 else check(REPO, ticker, today=today))
    except DenominationError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"refused: no DCF at {e}", file=sys.stderr)
        return 2

    print(json.dumps(block, indent=2))
    return 3 if block["passed"] is False else 0


if __name__ == "__main__":
    sys.exit(main())
