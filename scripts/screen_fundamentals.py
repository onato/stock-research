#!/usr/bin/env python3
"""Screen every researched ticker on fundamentals, freshly computed.

Named `screen_fundamentals` rather than `screen` because the screen-investments
skill already owns the top-level module name `screen` (select_ticker.py imports
it), and `make screen` already runs that DCF-upside ranking. This is the
different question: filter on the underlying financials rather than rank by
modelled upside.

Nothing is cached. Each run opens the ticker DBs read-only and derives
everything in memory, so the answer always reflects the current data.

A ticker that cannot be evaluated is reported as UNSCREENABLE with its
reasons, never omitted -- a screen that silently drops what it could not parse
reads as "these are the only candidates" when it means "these are the ones I
understood".

Usage:
  screen_fundamentals.py --exchange NZX --min-roe 0.15 --max-de 1 --min-fcf 0
  screen_fundamentals.py --suffix .NZ --min-revenue-growth-5y-total 0.5
"""

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import fundamentals

REPO = pathlib.Path(__file__).resolve().parents[1]

EXCHANGE_SUFFIX: dict[str, str] = {
    "NZX": ".NZ", "HKEX": ".HK", "LSE": ".L", "TSXV": ".V", "ASX": ".AX",
    "EURONEXT": ".AS",
}


@dataclass(frozen=True, slots=True)
class Criteria:
    """Thresholds to filter on. None means 'do not test this field'."""

    min_revenue_cagr_5y: float | None = None
    min_revenue_growth_5y_total: float | None = None
    min_earnings_cagr_5y: float | None = None
    min_revenue_growth_1y: float | None = None
    min_earnings_growth_1y: float | None = None
    min_roe: float | None = None
    min_de: float | None = None
    max_de: float | None = None
    min_fcf: float | None = None
    min_peg: float | None = None
    max_peg: float | None = None
    allow_fy_basis: bool = False

    def tests(self) -> list[tuple[str, str, float]]:
        """(field, comparison, threshold) for every criterion actually set."""
        spec: list[tuple[str, str, float | None]] = [
            ("revenue_cagr_5y", ">=", self.min_revenue_cagr_5y),
            ("revenue_growth_5y_total", ">=", self.min_revenue_growth_5y_total),
            ("earnings_cagr_5y", ">=", self.min_earnings_cagr_5y),
            ("revenue_growth_1y", ">=", self.min_revenue_growth_1y),
            ("earnings_growth_1y", ">=", self.min_earnings_growth_1y),
            ("roe", ">=", self.min_roe),
            ("debt_to_equity", ">=", self.min_de),
            ("debt_to_equity", "<=", self.max_de),
            ("ttm_fcf", ">=", self.min_fcf),
            ("peg", ">=", self.min_peg),
            ("peg", "<=", self.max_peg),
        ]
        return [(f, op, t) for f, op, t in spec if t is not None]


@dataclass
class Result:
    passed: list[fundamentals.Fundamentals] = field(default_factory=list)
    fy_basis: list[fundamentals.Fundamentals] = field(default_factory=list)
    failed: list[fundamentals.Fundamentals] = field(default_factory=list)
    unscreenable: list[fundamentals.Fundamentals] = field(default_factory=list)


def select(rows: list[fundamentals.Fundamentals], criteria: Criteria) -> Result:
    """Partition tickers into passed / FY-basis / failed / unscreenable.

    A row is unscreenable when a field the criteria actually test is missing.
    Fields nobody asked about are never a reason to exclude.
    """
    result = Result()
    tests = criteria.tests()

    for row in rows:
        missing = [f for f, _, _ in tests if getattr(row, f) is None]
        if missing or (tests and row.ttm_basis == "NONE"):
            result.unscreenable.append(row)
            continue

        ok = True
        for fname, op, threshold in tests:
            value = getattr(row, fname)
            if (op == ">=" and value < threshold) or (op == "<=" and value > threshold):
                ok = False
                break
        if not ok:
            result.failed.append(row)
        elif row.ttm_basis == "FY" and not criteria.allow_fy_basis:
            # A real TTM was not reconstructible, so "1Y TTM growth" would
            # silently mean "latest annual growth" for this row.
            result.fy_basis.append(row)
        else:
            result.passed.append(row)
    return result


def suffix_for(exchange: str | None, suffix: str | None) -> str | None:
    """Resolve --exchange / --suffix into a single ticker suffix."""
    resolved = None
    if exchange:
        key = exchange.strip().upper()
        if key not in EXCHANGE_SUFFIX:
            known = ", ".join(sorted(EXCHANGE_SUFFIX))
            raise ValueError(f"unknown exchange {exchange!r} (known: {known})")
        resolved = EXCHANGE_SUFFIX[key]
    if suffix:
        explicit = suffix if suffix.startswith(".") else f".{suffix}"
        if resolved and explicit != resolved:
            raise ValueError(
                f"--exchange {exchange} and --suffix {suffix} disagree "
                f"({resolved} vs {explicit})")
        resolved = explicit
    return resolved


def cagr_note(threshold: float | None) -> str | None:
    """Warn when a 5Y CAGR threshold looks like it meant total growth.

    0.5 as a CAGR compounds to 7.6x over five years, which is almost never
    what someone screening for "5Y growth > 0.5" has in mind.
    """
    if threshold is None or threshold <= 0.35:
        return None
    multiple = (1 + threshold) ** 5
    return (f"note: {threshold:.2f} as a 5Y CAGR means {multiple:.1f}x over five "
            f"years; use --min-revenue-growth-5y-total if you meant total growth.")


def _fmt(value: float | None, places: int = 3) -> str:
    return "-" if value is None else f"{value:.{places}f}"


COLUMNS = "  {:<10s} {:>6s} {:>10s} {:>9s} {:>8s} {:>8s} {:>7s} {:>6s} {:>7s}"
HEADERS = ("ticker", "ccy", "ttmRev", "ttmFCF", "g5yTot", "g1y", "ROE", "D/E", "PEG")


def _row_line(row: fundamentals.Fundamentals) -> str:
    return COLUMNS.format(
        row.ticker, row.currency or "?",
        _fmt(row.ttm_revenue, 1), _fmt(row.ttm_fcf, 1),
        _fmt(row.revenue_growth_5y_total), _fmt(row.revenue_growth_1y),
        _fmt(row.roe), _fmt(row.debt_to_equity, 2), _fmt(row.peg, 2))


def unscreenable_line(row: fundamentals.Fundamentals, criteria: Criteria) -> str:
    """Why this ticker could not be evaluated, in the caller's terms.

    Prefers the derivation's own reasons; falls back to naming the screened
    fields that came back empty, because "missing a screened field" does not
    tell anyone which one or what to fix.
    """
    if row.reasons:
        return ", ".join(row.reasons)
    missing = sorted({f for f, _, _ in criteria.tests() if getattr(row, f) is None})
    if missing:
        return "no value for: " + ", ".join(missing)
    return "no trailing-twelve-month basis"


def report(result: Result, criteria: Criteria) -> None:
    """Print all four blocks. Nothing is truncated."""
    print(f"\n  PASS ({len(result.passed)})\n")
    if result.passed:
        print(COLUMNS.format(*HEADERS))
        print("  " + "-" * 78)
        for row in sorted(result.passed, key=lambda r: -(r.roe or 0)):
            print(_row_line(row))
    else:
        print("    (none)")

    if result.fy_basis:
        print(f"\n  FY-BASIS ({len(result.fy_basis)}) -- no true TTM available; "
              f"pass --allow-fy-basis to include\n")
        for row in result.fy_basis:
            print(_row_line(row))

    print(f"\n  UNSCREENABLE ({len(result.unscreenable)}) -- reported, not dropped\n")
    for row in sorted(result.unscreenable, key=lambda r: r.ticker):
        print(f"    {row.ticker:<10s} {unscreenable_line(row, criteria)}")

    print(f"\n  {len(result.failed)} ticker(s) evaluated and filtered out.")
    if criteria.min_peg is not None or criteria.max_peg is not None:
        print("  PEG uses the DCF's selected_growth_rate as a forward proxy "
              "-- not an analyst estimate.")
    print("  Cross-currency values are NOT FX-converted -- compare within a "
          "currency.\n")


def _to_json(result: Result) -> str:
    def dump(rows: list[fundamentals.Fundamentals]) -> list[dict[str, Any]]:
        return [{"ticker": r.ticker, "currency": r.currency,
                 "ttm_revenue": r.ttm_revenue, "ttm_net_income": r.ttm_net_income,
                 "ttm_fcf": r.ttm_fcf, "ttm_basis": r.ttm_basis,
                 "revenue_growth_5y_total": r.revenue_growth_5y_total,
                 "revenue_cagr_5y": r.revenue_cagr_5y,
                 "revenue_growth_1y": r.revenue_growth_1y,
                 "earnings_growth_1y": r.earnings_growth_1y,
                 "roe": r.roe, "debt_to_equity": r.debt_to_equity,
                 "derived_eps": r.derived_eps, "peg": r.peg,
                 "reasons": list(r.reasons)} for r in rows]

    return json.dumps({"passed": dump(result.passed),
                       "fy_basis": dump(result.fy_basis),
                       "failed": dump(result.failed),
                       "unscreenable": dump(result.unscreenable)}, indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exchange", help="NZX, HKEX, LSE, TSXV, ASX, EURONEXT")
    ap.add_argument("--suffix", help="raw ticker suffix, e.g. .NZ")
    ap.add_argument("--ticker", nargs="*", default=[])
    ap.add_argument("--min-revenue-cagr-5y", type=float)
    ap.add_argument("--min-revenue-growth-5y-total", type=float)
    ap.add_argument("--min-earnings-cagr-5y", type=float)
    ap.add_argument("--min-revenue-growth-1y", type=float)
    ap.add_argument("--min-earnings-growth-1y", type=float)
    ap.add_argument("--min-roe", type=float)
    ap.add_argument("--min-de", type=float)
    ap.add_argument("--max-de", type=float)
    ap.add_argument("--min-fcf", type=float)
    ap.add_argument("--min-peg", type=float)
    ap.add_argument("--max-peg", type=float)
    ap.add_argument("--allow-fy-basis", action="store_true")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--root")
    args = ap.parse_args(argv)

    try:
        suffix = suffix_for(args.exchange, args.suffix)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    criteria = Criteria(
        min_revenue_cagr_5y=args.min_revenue_cagr_5y,
        min_revenue_growth_5y_total=args.min_revenue_growth_5y_total,
        min_earnings_cagr_5y=args.min_earnings_cagr_5y,
        min_revenue_growth_1y=args.min_revenue_growth_1y,
        min_earnings_growth_1y=args.min_earnings_growth_1y,
        min_roe=args.min_roe, min_de=args.min_de, max_de=args.max_de,
        min_fcf=args.min_fcf, min_peg=args.min_peg, max_peg=args.max_peg,
        allow_fy_basis=args.allow_fy_basis)

    note = cagr_note(args.min_revenue_cagr_5y)
    if note:
        print(note, file=sys.stderr)

    root = pathlib.Path(args.root) if args.root else REPO
    rows = fundamentals.scan(root, suffix=suffix, tickers=set(args.ticker))
    result = select(rows, criteria)
    report(result, criteria)

    if args.json_out:
        pathlib.Path(args.json_out).write_text(_to_json(result))
        print(f"  wrote {args.json_out}\n")

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
