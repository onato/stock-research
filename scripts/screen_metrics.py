#!/usr/bin/env python3
"""Query core metrics across every researched ticker, on one scale.

The per-ticker DuckDBs solve schema drift, but comparing them still needs
care: SEC XBRL yields absolute dollars, NZX filings are usually thousands,
and older CSVs are in millions. The metrics_normalized view fixes the
scale per database; this joins them.

It opens each database separately rather than ATTACHing them together --
the view references core_metrics unqualified, so several attached catalogs
make that name ambiguous.

Usage:
  screen_metrics.py --period FY2024 --order revenue
  screen_metrics.py --metric net_income --period FY2024 --top 20
  screen_metrics.py --ticker NFLX PYPL AGL.NZ
"""

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

NUMERIC = ["revenue", "gross_profit", "operating_income", "ebitda",
           "net_income", "operating_cash_flow", "free_cash_flow",
           "shareholders_equity", "total_assets", "total_debt",
           "cash_and_equivalents", "stock_based_comp", "eps"]


def load(tickers, period):
    import duckdb
    rows = []
    for db in sorted(REPO.glob("research/*/Reports/*.duckdb")):
        t = db.parent.parent.name
        if tickers and t not in tickers:
            continue
        try:
            con = duckdb.connect(str(db), read_only=True)
            cols = ", ".join(NUMERIC)
            q = f"SELECT period, currency, {cols} FROM metrics_normalized"
            if period:
                q += " WHERE period = ?"
                res = con.execute(q, [period]).fetchall()
            else:
                res = con.execute(q).fetchall()
            names = ["period", "currency", *NUMERIC]
            for r in res:
                rows.append({"ticker": t, **dict(zip(names, r))})
            con.close()
        except Exception:
            # A DB without the view (never re-extracted) is skipped rather
            # than failing the whole screen.
            continue
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="FY2024")
    ap.add_argument("--metric", default="revenue", choices=NUMERIC)
    ap.add_argument("--order", default="")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--ticker", nargs="*", default=[])
    args = ap.parse_args()

    key = args.order or args.metric
    rows = [r for r in load(set(args.ticker), args.period)
            if r.get(key) is not None]
    if not rows:
        print(f"no data for period {args.period}", file=sys.stderr)
        print("  (only re-extracted tickers have the normalized view)",
              file=sys.stderr)
        return 1

    rows.sort(key=lambda r: -abs(r[key]))
    print(f"  {args.period} — {key}, millions of reporting currency\n")
    print(f"  {'ticker':10s} {key:>18s} {'ccy':>5s} {'net_income':>14s} {'eps':>8s}")
    print("  " + "-" * 60)
    for r in rows[:args.top]:
        ni = r.get("net_income")
        eps = r.get("eps")
        print(f"  {r['ticker']:10s} {r[key]:18,.1f} {r['currency'] or '?'!s:>5s} "
              f"{(f'{ni:,.1f}' if ni is not None else '-'):>14s} "
              f"{(f'{eps:.2f}' if eps is not None else '-'):>8s}")
    print(f"\n  {len(rows)} ticker(s) with data for {args.period}")
    print("  Cross-currency values are NOT FX-converted -- compare within a currency.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
