#!/usr/bin/env python3
"""Derive comparable fundamentals for one ticker from its normalised metrics.

Reads the warehouse's `metrics_m` view (state/research.duckdb, `make
warehouse`), never a raw table: the view is the only surface where money
columns share a scale. The warehouse is built from the committed Metrics CSVs
and DCF JSONs, so the screen sees exactly what is committed -- until
2026-09-22 it opened every ticker's gitignored cache DB instead, and a fresh
checkout screened nothing.

Three corpus hazards shape the arithmetic here, all of which produce plausible
wrong answers rather than obvious ones:

  * **Half-yearly reporters.** Every NZX filer reports FY + H1, never
    quarters, so a TTM is `FY(Y-1) + H1(Y) - H1(Y-1)` -- not a sum of four.
  * **The `eps` column is unusable.** 13 tickers store EPS in cents (WISE.L,
    ANZ.NZ, AIA.NZ, ATM.NZ, EBO.NZ, SPK.NZ, ARG.NZ, AFI.NZ, OCA.NZ, SDL.NZ,
    9999.HK, MELI) and 5 have a shares-scale bug (AFT.NZ, APL.NZ, DCBO, XPEL,
    FIG). metrics_normalized deliberately leaves per-share figures unscaled,
    so EPS is always re-derived from two normalised columns.
  * **Price currency.** WISE.L quotes GBP pence while its financials and its
    DCF both say USD. `885.6 / 48.43` yields a P/E of 18.3 -- pence over cents
    approximates GBP over USD only by coincidence.

Every value that cannot be computed is None with a reason string attached, so
the screener can say why a ticker dropped out instead of silently omitting it.
"""

import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import periods

REPO = pathlib.Path(__file__).resolve().parents[1]

TtmBasis = Literal["4Q", "FY+H1", "FY", "NONE"]

# Ordered weakest-last: a ticker is only as trustworthy as its softest field.
_BASIS_RANK: dict[str, int] = {"4Q": 0, "FY+H1": 1, "FY": 2, "NONE": 3}

# Money and count columns pulled from the view. Per-share and percentage
# columns are deliberately absent -- see the `eps` note above.
NUMERIC = ["revenue", "net_income", "free_cash_flow", "shareholders_equity",
           "total_debt", "shares_outstanding", "operating_cash_flow", "ebitda"]

# The DCF's chosen forward-growth proxy. Stored as a PERCENT (18.96 == 18.96%).
# SDL.NZ spells the key differently; both are the same quantity.
_GROWTH_KEYS = ("selected_growth_rate", "historical_cagr")

# Quote denominations that are a hundredth of their currency. An LSE price is
# quoted in pence against filings in pounds, so the two are the same currency
# at different scales -- converting lets the ordinary currency comparison do
# the work. WISE.L's DCF records `price_currency: "GBp"` explicitly, which is
# why this is read rather than inferred from the ticker suffix.
MINOR_UNITS: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0),
    "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0),
}


@dataclass(frozen=True, slots=True)
class Fundamentals:
    """Everything the screener filters on, plus why anything is missing."""

    ticker: str
    currency: str | None = None
    ttm_revenue: float | None = None
    ttm_net_income: float | None = None
    ttm_fcf: float | None = None
    ttm_basis: TtmBasis = "NONE"
    revenue_cagr_5y: float | None = None
    earnings_cagr_5y: float | None = None
    revenue_growth_5y_total: float | None = None
    earnings_growth_5y_total: float | None = None
    revenue_growth_1y: float | None = None
    earnings_growth_1y: float | None = None
    roe: float | None = None
    debt_to_equity: float | None = None
    derived_eps: float | None = None
    peg: float | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _num(value: Any) -> float | None:
    """A value is usable only if it is a real number. None is not zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _by_period(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    """Canonical period label -> value, for rows where the value is present."""
    out: dict[str, float] = {}
    for r in rows:
        p = periods.parse(r.get("period"))
        if p.ptype == "OTHER" or p.fiscal_year is None:
            # Irregular windows (15-month years, stubs) are never comparable.
            continue
        v = _num(r.get(key))
        if v is not None:
            out[periods.canonical(p)] = v
    return out


def _years(values: dict[str, float]) -> list[int]:
    """Fiscal years present, ignoring any label that has none."""
    return [y for y in (periods.parse(k).fiscal_year for k in values)
            if y is not None]


def _latest_year(values: dict[str, float]) -> int | None:
    years = _years(values)
    return max(years) if years else None


def _ttm_paths(values: dict[str, float], year: int) -> list[tuple[float, TtmBasis]]:
    """Every way to build the trailing twelve months ending in fiscal `year`,
    strongest first: four quarters; the completed year (the twelve months to
    year-end, newer than a reconstruction that ends at its own H1 -- but tagged
    FY, since no interim confirms nothing has moved since); FY(Y-1) + H1(Y)
    - H1(Y-1) for half-yearly reporters."""
    out: list[tuple[float, TtmBasis]] = []
    quarters = [values.get(f"Q{i} FY{year}") for i in (1, 2, 3, 4)]
    if all(q is not None for q in quarters):
        out.append((sum(q for q in quarters if q is not None), "4Q"))
    else:
        present = [i for i in (1, 2, 3, 4) if values.get(f"Q{i} FY{year}") is not None]
        if present:
            # Walk back from the newest quarter, allowing a year rollover, and
            # require four consecutive quarters with no hole.
            window: list[float] = []
            qi, yr = max(present), year
            while len(window) < 4:
                v = values.get(f"Q{qi} FY{yr}")
                if v is None:
                    break
                window.append(v)
                qi -= 1
                if qi == 0:
                    qi, yr = 4, yr - 1
            if len(window) == 4:
                out.append((sum(window), "4Q"))

    full = values.get(f"FY{year}")
    if full is not None:
        out.append((full, "FY"))

    prior_fy = values.get(f"FY{year - 1}")
    this_h1 = values.get(f"H1 FY{year}")
    prior_h1 = values.get(f"H1 FY{year - 1}")
    if prior_fy is not None and this_h1 is not None and prior_h1 is not None:
        out.append((prior_fy + this_h1 - prior_h1, "FY+H1"))
    return out


def _ttm_at(values: dict[str, float], year: int,
            want: TtmBasis | None = None) -> tuple[float | None, TtmBasis]:
    """Trailing twelve months ending in fiscal `year` by the strongest path,
    or by the one path `want` names (a prior-period comparison must use the
    same basis as the current one)."""
    for value, basis in _ttm_paths(values, year):
        if want is None or basis == want:
            return value, basis
    return None, "NONE"


def _ttm_resolved(values: dict[str, float]) -> tuple[float | None, TtmBasis, int | None]:
    """TTM plus the fiscal year it was actually built from.

    The anchor year matters: MSFT's newest year holds only Q1-Q3, so the TTM
    falls back to the FY below it. A prior-period comparison must step back
    from THAT year, not from the newest label present.
    """
    if not values:
        return None, "NONE", None
    year = _latest_year(values)
    if year is None:
        return None, "NONE", None

    # The newest year may hold only a stray interim (an H2, or a lone Q2).
    # Walk back until a year yields a real window rather than declaring the
    # field missing -- but never past the data itself.
    oldest = min(_years(values), default=year)
    for candidate in range(year, oldest - 1, -1):
        value, basis = _ttm_at(values, candidate)
        if basis != "NONE":
            return value, basis, candidate
    return None, "NONE", None


def ttm(rows: list[dict[str, Any]], key: str) -> tuple[float | None, TtmBasis]:
    """Trailing twelve months for one field, with the basis actually used."""
    value, basis, _ = _ttm_resolved(_by_period(rows, key))
    return value, basis


def _prior_ttm(values: dict[str, float], basis: TtmBasis) -> float | None:
    """The same reconstruction one fiscal year before the one the TTM used.

    Mixing a TTM numerator with an FY denominator fabricates growth, so the
    prior period must use the identical basis or the comparison is refused.
    """
    _, current_basis, anchor = _ttm_resolved(values)
    if anchor is None or basis == "NONE" or current_basis != basis:
        return None
    value, _ = _ttm_at(values, anchor - 1, want=basis)
    return value


def _cagr(values: dict[str, float], years: int,
          reasons: list[str], label: str) -> tuple[float | None, float | None]:
    """(CAGR, total growth) between two genuine full years `years` apart."""
    # is_annual() guarantees a real 12-month year, so fiscal_year is never
    # None here -- but only build the map from parses that prove it.
    annual: dict[int, float] = {}
    for period_label, value in values.items():
        p = periods.parse(period_label)
        if periods.is_annual(p) and p.fiscal_year is not None:
            annual[p.fiscal_year] = value
    if not annual:
        return None, None
    end_year = max(annual)
    start = annual.get(end_year - years)
    end = annual.get(end_year)
    if start is None or end is None:
        return None, None
    if start <= 0:
        # A negative or zero base makes the ratio meaningless and the root
        # complex. AGL.NZ's earnings hit this routinely.
        reasons.append(f"cagr-nonpositive-base:{label}")
        return None, None
    total = end / start - 1
    return (end / start) ** (1 / years) - 1, total


def _growth(current: float | None, prior: float | None,
            reasons: list[str], label: str) -> float | None:
    """Period-on-period growth, refused when the base is not positive.

    A percentage measured from a loss is meaningless: a swing from -8 to +30
    would read as a large positive growth, and the sign flips unpredictably.
    SEK.NZ hits this via its FY2023 loss.
    """
    if current is None or prior is None:
        return None
    if prior <= 0:
        reasons.append(f"growth-nonpositive-base:{label}")
        return None
    return current / prior - 1


def _growth_rate_pct(dcf: dict[str, Any] | None) -> float | None:
    hg = (dcf or {}).get("historical_growth") or {}
    for key in _GROWTH_KEYS:
        v = _num(hg.get(key))
        if v is not None:
            return v
    return None


def compute(ticker: str, rows: list[dict[str, Any]],
            dcf: dict[str, Any] | None = None) -> Fundamentals:
    """Derive every screening field for one ticker."""
    reasons: list[str] = []

    if not rows:
        return Fundamentals(ticker, reasons=("no-core-metrics",))

    currencies = {str(r["currency"]) for r in rows if r.get("currency")}
    currency = min(currencies) if currencies else None
    if len(currencies) > 1:
        reasons.append("mixed-currency:" + ",".join(sorted(currencies)))

    rev = _by_period(rows, "revenue")
    ni = _by_period(rows, "net_income")
    fcf = _by_period(rows, "free_cash_flow")
    equity = _by_period(rows, "shareholders_equity")
    debt = _by_period(rows, "total_debt")
    shares = _by_period(rows, "shares_outstanding")

    ttm_rev, rev_basis = ttm(rows, "revenue")
    ttm_ni, ni_basis = ttm(rows, "net_income")
    ttm_fcf, fcf_basis = ttm(rows, "free_cash_flow")

    # metrics_normalized resolves unknown units to NULL across every money
    # column at once. Reporting that as missing filings blames the source for
    # what is really an unresolved scale -- AGL.NZ has 18 revenue rows and no
    # units, and the fix is backfill_units, not re-extraction.
    if not (rev or ni or fcf or equity or debt):
        reasons.append("units-unresolved")
    else:
        for value, name in ((ttm_rev, "revenue"), (ttm_ni, "net_income"),
                            (ttm_fcf, "free_cash_flow")):
            if value is None:
                reasons.append(f"no-ttm:{name}")

    # The row is only as strong as its weakest reconstruction -- but only
    # across fields that actually reconstructed. A field with no data at all
    # is reported via its own `no-ttm:` reason; letting it force the basis to
    # NONE would hide whether the fields that DO exist are TTM or FY-based.
    computed = [b for b in (rev_basis, ni_basis, fcf_basis) if b != "NONE"]
    basis: TtmBasis = (max(computed, key=lambda b: _BASIS_RANK[b])
                       if computed else "NONE")

    rev_cagr, rev_total = _cagr(rev, 5, reasons, "revenue")
    eps_cagr, eps_total = _cagr(ni, 5, reasons, "net_income")

    rev_growth = _growth(ttm_rev, _prior_ttm(rev, rev_basis), reasons, "revenue")
    ni_growth = _growth(ttm_ni, _prior_ttm(ni, ni_basis), reasons, "net_income")

    # ROE on AVERAGE equity: NZ balance sheets jump on capital raises (AGL.NZ
    # doubled its share count in FY2026), and point-in-time equity would
    # understate the return that produced it.
    roe = None
    if equity:
        ordered = sorted(equity, key=periods.sort_key)
        latest_equity = equity[ordered[-1]]
        if len(ordered) >= 2:
            avg_equity = (equity[ordered[-2]] + latest_equity) / 2
        else:
            avg_equity = latest_equity
            reasons.append("roe-point-equity")
        if ttm_ni is not None and avg_equity > 0:
            roe = ttm_ni / avg_equity
        elif avg_equity <= 0:
            reasons.append("roe-nonpositive-equity")

    # Debt/equity is point-in-time: both legs must come from the same period.
    de = None
    if equity and debt:
        latest = max(set(equity) & set(debt), key=periods.sort_key, default=None)
        if latest is not None:
            eq = equity[latest]
            if eq > 0:
                de = debt[latest] / eq
            else:
                reasons.append("negative-equity")

    # EPS is derived, never read from the stored column (see module docstring).
    eps = None
    if shares:
        latest_sh = shares[max(shares, key=periods.sort_key)]
        if ttm_ni is not None and latest_sh > 0:
            eps = ttm_ni / latest_sh

    # Price must be denominated the same way the financials are, or the P/E
    # is a coincidence rather than a measurement. `price_currency` names the
    # QUOTE's currency and `currency` the filings' -- WISE.L quotes GBp while
    # reporting USD, so conflating the two hides the mismatch entirely.
    price = _num((dcf or {}).get("current_price"))
    quote_currency = (dcf or {}).get("price_currency") or (dcf or {}).get("currency")
    if price is not None:
        price, quote_currency = to_major_unit(price, quote_currency)

    price_ok = True
    if (price is not None and currency and quote_currency
            and quote_currency != currency):
        reasons.append("price-currency-mismatch")
        price_ok = False

    peg = None
    growth_pct = _growth_rate_pct(dcf)
    if price is not None and price_ok and eps is not None and eps > 0:
        if growth_pct is None:
            reasons.append("peg-no-dcf-growth")
        elif growth_pct <= 0:
            reasons.append("peg-nonpositive-growth")
        else:
            peg = (price / eps) / growth_pct

    return Fundamentals(
        ticker=ticker, currency=currency,
        ttm_revenue=ttm_rev, ttm_net_income=ttm_ni, ttm_fcf=ttm_fcf,
        ttm_basis=basis,
        revenue_cagr_5y=rev_cagr, earnings_cagr_5y=eps_cagr,
        revenue_growth_5y_total=rev_total, earnings_growth_5y_total=eps_total,
        revenue_growth_1y=rev_growth, earnings_growth_1y=ni_growth,
        roe=roe, debt_to_equity=de, derived_eps=eps, peg=peg,
        reasons=tuple(reasons),
    )


def to_major_unit(price: float, quote_currency: str | None) -> tuple[float, str | None]:
    """Convert a minor-unit quote to its major unit: 885.6 GBp -> 8.856 GBP.

    Only the denomination changes, never the currency, so the ordinary
    `price_currency != currency` comparison can then decide the rest. That
    keeps minor units out of the mismatch rule instead of beside it.
    """
    if not quote_currency:
        return price, quote_currency
    factor = MINOR_UNITS.get(quote_currency.strip())
    if factor is None:
        return price, quote_currency
    major, divisor = factor
    return price / divisor, major


WAREHOUSE = pathlib.Path("state") / "research.duckdb"


def load_dcf(repo: pathlib.Path, ticker: str) -> dict[str, Any] | None:
    """The ticker's DCF file, or None if absent or unreadable (backfill_units
    reads the file directly; the screen reads the warehouse's copy)."""
    path = repo / "research" / ticker / "Reports" / f"{ticker}_DCF.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_all(repo: pathlib.Path) -> dict[str, tuple[list[dict[str, Any]], dict[str, Any] | None]]:
    """{ticker: (metrics_m rows, DCF document or None)} for every researched
    ticker in the warehouse."""
    import duckdb

    db = repo / WAREHOUSE
    if not db.exists():
        raise FileNotFoundError(f"{db} does not exist: run `make warehouse` first")
    cols = ", ".join(NUMERIC)
    con = duckdb.connect(str(db), read_only=True)
    try:
        researched = con.execute(
            "SELECT ticker FROM companies WHERE has_metrics OR has_dcf").fetchall()
        metrics = con.execute(
            f"SELECT ticker, period, currency, {cols} FROM metrics_m ORDER BY ticker").fetchall()
        docs = con.execute("SELECT ticker, doc FROM dcf").fetchall()
    finally:
        con.close()
    names = ["period", "currency", *NUMERIC]
    # Every researched ticker gets an entry, so one with a DCF but no metrics
    # is reported as unscreenable rather than silently absent.
    out: dict[str, tuple[list[dict[str, Any]], dict[str, Any] | None]] = {
        t: ([], None) for (t,) in researched}
    for ticker, *rest in metrics:
        out.setdefault(ticker, ([], None))[0].append(dict(zip(names, rest, strict=True)))
    for ticker, doc in docs:
        if ticker not in out:
            continue
        try:
            data = json.loads(doc) if isinstance(doc, str) else None
        except ValueError:
            data = None
        out[ticker] = (out[ticker][0], data if isinstance(data, dict) else None)
    return out


def scan(repo: pathlib.Path | None = None, suffix: str | None = None,
         tickers: set[str] | None = None) -> list[Fundamentals]:
    """Derive fundamentals for every ticker with metrics in the warehouse,
    freshly each call."""
    root = repo or REPO
    out: list[Fundamentals] = []
    for ticker, (rows, dcf) in sorted(load_all(root).items()):
        if suffix and not ticker.endswith(suffix):
            continue
        if tickers and ticker not in tickers:
            continue
        out.append(compute(ticker, rows, dcf))
    return out
