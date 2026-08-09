#!/usr/bin/env python3
"""Delete pre-history rows from the metrics CSVs.

A row like PINS FY2016 -- a single equity figure carried in from a 10-K
comparative, for a year the company was still private -- is not a data point.
It cannot be filled by re-extraction (PINS's oldest filing is FY2019), and
leaving it in makes every report count seven phantom gaps against the ticker.

Which years to drop comes from `integrity_report.reporting_span()`, so this
tool and the reports agree by construction rather than by a second heuristic
kept in sync by hand.

What this adds is the safety rule. The span heuristic decides a year looks
like pre-history; deletion is destructive, so a row is only removed when it
carries nothing worth keeping. Any core-8 value other than ShareholdersEquity
blocks it -- equity alone is the comparative-carry signature -- and so does
any company KPI. If the heuristic is ever loosened, it still cannot eat real
data.

Usage:
  prune_stub_rows.py --check [TICKER ...]   # report, write nothing (default)
  prune_stub_rows.py --write [TICKER ...]   # delete the rows
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import integrity_report as ir
import schema

REPO = pathlib.Path(__file__).resolve().parents[1]

# A balance-sheet figure restated in a later filing's comparative column is
# exactly what a pre-history row carries, so equity alone does not protect a
# row. Every other core metric does.
CARRYABLE = {"shareholders_equity", "total_debt", "cash_and_equivalents",
             "total_assets", "total_liabilities"}


def _rows(path: pathlib.Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return list(reader.fieldnames or []), list(reader)
    except (OSError, UnicodeDecodeError, csv.Error):
        return [], []


def _worth_keeping(row: dict[str, str]) -> dict[str, str]:
    """Values in this row that would be lost by deleting it.

    Equity-style balance-sheet carries are excluded: they are the signature of
    a comparative-column artifact, not of a year the company reported.
    """
    keep: dict[str, str] = {}
    for header, value in row.items():
        if not header or not (value or "").strip():
            continue
        # Units/Currency label the other numbers rather than being data, and
        # every row carries them -- counting them as content would protect
        # every row and make the tool a no-op.
        name = ir.core_field(header) or schema.normalize(header)
        if name in ir.NON_METRIC or name in CARRYABLE:
            continue
        keep[header] = value.strip()
    return keep


def plan_file(path: pathlib.Path) -> dict[str, Any]:
    """Which periods would be deleted, and what each row holds."""
    _, rows = _rows(path)
    plan: dict[str, Any] = {"path": str(path), "periods": [], "values": [],
                            "blocked": []}
    if not rows:
        return plan

    by_year: dict[int, dict[str, str]] = {}
    for row in rows:
        period = row.get("Period") or ""
        if not ir.is_fy(period):
            continue
        y = ir.year_of(period)
        if y is not None:
            by_year.setdefault(y, row)

    for year in ir.reporting_span(by_year):
        row = by_year[year]
        keep = _worth_keeping(row)
        if keep:
            # Real data in a year the span called pre-history: leave it and
            # say so, rather than deleting something the heuristic misread.
            plan["blocked"].append((row.get("Period") or f"FY{year}", keep))
            continue
        plan["periods"].append(row.get("Period") or f"FY{year}")
        plan["values"].append({
            h: v.strip() for h, v in row.items()
            if h and h != "Period" and (v or "").strip()})
    plan["years"] = [y for y in ir.reporting_span(by_year)
                     if not _worth_keeping(by_year[y])]
    return plan


def prune_file(path: pathlib.Path) -> list[str]:
    """Delete the pre-history rows. Returns the periods removed."""
    plan = plan_file(path)
    if not plan["periods"]:
        return []

    header, rows = _rows(path)
    drop_years = set(plan["years"])
    kept = [r for r in rows
            if ir.year_of(r.get("Period") or "") not in drop_years]

    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(kept)
    return list(plan["periods"])


def discover(tickers: list[str]) -> list[pathlib.Path]:
    if tickers:
        return [REPO / "research" / t / "Reports" / f"{t}_Metrics.csv"
                for t in tickers]
    return sorted(REPO.glob("research/*/Reports/*_Metrics.csv"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("tickers", nargs="*", help="default: every ticker")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="report what would be deleted (default)")
    mode.add_argument("--write", action="store_true",
                      help="delete the rows")
    args = p.parse_args()

    paths = [q for q in discover(args.tickers) if q.is_file()]
    if not paths:
        print("no Metrics CSVs found", file=sys.stderr)
        return 1

    n_rows = 0
    for path in paths:
        ticker = path.parent.parent.name
        plan = plan_file(path)
        for period, keep in plan["blocked"]:
            print(f"  {ticker:10} {period} KEPT -- holds {keep}")
        if not plan["periods"]:
            continue
        for period, values in zip(plan["periods"], plan["values"],
                                  strict=True):
            n_rows += 1
            print(f"  {ticker:10} {period} -> {values or '{} (empty row)'}")
        if args.write:
            prune_file(path)

    verb = "deleted" if args.write else "would delete"
    print(f"\n{verb} {n_rows} row(s)")
    if not args.write and n_rows:
        print("(--check mode: nothing written. Re-run with --write to apply.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
