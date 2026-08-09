#!/usr/bin/env python3
"""Derive comparable fundamentals for one ticker from its normalised metrics.

Reads `metrics_normalized`, never `core_metrics`: the view is the only surface
where money columns share a scale. Tickers are opened one database at a time
and unioned client-side, because the view references `core_metrics` unqualified
and ATTACHing several catalogs makes that name ambiguous (see schema.py).

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

_QUARTERS = ("Q1", "Q2", "Q3", "Q4")

# The DCF's chosen forward-growth proxy. Stored as a PERCENT (18.96 == 18.96%).
# SDL.NZ spells the key differently; both are the same quantity.
_GROWTH_KEYS = ("selected_growth_rate", "historical_cagr")


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
    price: float | None = None
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


def _ttm_at(values: dict[str, float], year: int) -> tuple[float | None, TtmBasis]:
    """Trailing twelve months ending in fiscal `year`, by the strongest path."""
    # 1. Four quarters of the year, or the trailing four across its boundary.
    quarters = [values.get(f"Q{i} FY{year}") for i in (1, 2, 3, 4)]
    if all(q is not None for q in quarters):
        return sum(q for q in quarters if q is not None), "4Q"

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
            return sum(window), "4Q"

    # 2. Half-yearly reporters: FY(Y-1) + H1(Y) - H1(Y-1).
    prior_fy = values.get(f"FY{year - 1}")
    this_h1 = values.get(f"H1 FY{year}")
    prior_h1 = values.get(f"H1 FY{year - 1}")
    if prior_fy is not None and this_h1 is not None and prior_h1 is not None:
        return prior_fy + this_h1 - prior_h1, "FY+H1"

    # 3. The latest full year, which is not a TTM -- callers must tag it.
    full = values.get(f"FY{year}")
    if full is not None:
        return full, "FY"

    return None, "NONE"


def ttm(rows: list[dict[str, Any]], key: str) -> tuple[float | None, TtmBasis]:
    """Trailing twelve months for one field, with the basis actually used."""
    values = _by_period(rows, key)
    if not values:
        return None, "NONE"

    year = _latest_year(values)
    if year is None:
        return None, "NONE"

    # The newest year may hold only a stray interim (an H2, or a lone Q2).
    # Walk back until a year yields a real window rather than declaring the
    # field missing -- but never past the data itself.
    oldest = min(_years(values), default=year)
    for candidate in range(year, oldest - 1, -1):
        value, basis = _ttm_at(values, candidate)
        if basis != "NONE":
            return value, basis
    return None, "NONE"


def _prior_ttm(values: dict[str, float], basis: TtmBasis) -> float | None:
    """The same reconstruction one fiscal year earlier.

    Mixing a TTM numerator with an FY denominator fabricates growth, so the
    prior period must use the identical basis or the comparison is refused.
    """
    year = _latest_year(values)
    if year is None or basis == "NONE":
        return None
    value, prior_basis = _ttm_at(values, year - 1)
    return value if prior_basis == basis else None


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


def _growth(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior <= 0:
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
            dcf: dict[str, Any] | None = None,
            price: float | None = None,
            price_currency: str | None = None) -> Fundamentals:
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
    equity = _by_period(rows, "shareholders_equity")
    debt = _by_period(rows, "total_debt")
    shares = _by_period(rows, "shares_outstanding")

    ttm_rev, rev_basis = ttm(rows, "revenue")
    ttm_ni, ni_basis = ttm(rows, "net_income")
    ttm_fcf, fcf_basis = ttm(rows, "free_cash_flow")

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

    rev_growth = _growth(ttm_rev, _prior_ttm(rev, rev_basis))
    ni_growth = _growth(ttm_ni, _prior_ttm(ni, ni_basis))

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
    # is a coincidence rather than a measurement.
    if price is None:
        price = _num((dcf or {}).get("current_price"))
    if price_currency is None:
        price_currency = (dcf or {}).get("currency")

    price_ok = True
    if price is not None and (
            (bool(currency) and bool(price_currency) and price_currency != currency)
            or _is_minor_unit(ticker, price, eps)):
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
        price=price if price_ok else None,
        reasons=tuple(reasons),
    )


def _is_minor_unit(ticker: str, price: float, eps: float | None) -> bool:
    """True when a quote looks like pence/cents against major-unit earnings.

    LSE prices are quoted in pence while filings report pounds (or, for
    WISE.L, US dollars). A 100x gap between price and earnings-per-share is
    the signature; 885.6 against an EPS of 0.4843 is a P/E of 1,829, not 18.
    """
    if not ticker.endswith(".L") or eps is None or eps <= 0:
        return False
    return price / eps > 200


def load_rows(db: pathlib.Path) -> list[dict[str, Any]]:
    """Read metrics_normalized from one ticker database, read-only."""
    import duckdb

    cols = ", ".join(NUMERIC)
    con = duckdb.connect(str(db), read_only=True)
    try:
        res = con.execute(
            f"SELECT period, currency, {cols} FROM metrics_normalized").fetchall()
    finally:
        con.close()
    names = ["period", "currency", *NUMERIC]
    return [dict(zip(names, r, strict=True)) for r in res]


def load_dcf(repo: pathlib.Path, ticker: str) -> dict[str, Any] | None:
    import json

    path = repo / "research" / ticker / "Reports" / f"{ticker}_DCF.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def scan(repo: pathlib.Path | None = None, suffix: str | None = None,
         tickers: set[str] | None = None) -> list[Fundamentals]:
    """Derive fundamentals for every researched ticker, freshly each call."""
    root = repo or REPO
    out: list[Fundamentals] = []
    for db in sorted(root.glob("research/*/Reports/*.duckdb")):
        ticker = db.parent.parent.name
        if suffix and not ticker.endswith(suffix):
            continue
        if tickers and ticker not in tickers:
            continue
        try:
            rows = load_rows(db)
        except Exception as exc:  # a corrupt or view-less DB must not kill the run
            out.append(Fundamentals(ticker, reasons=(f"db-unreadable:{type(exc).__name__}",)))
            continue
        out.append(compute(ticker, rows, load_dcf(root, ticker)))
    return out
