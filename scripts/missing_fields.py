#!/usr/bin/env python3
"""Name the specific gaps in the metrics CSVs, machine-readably.

`integrity_report.py` answers "how complete is this ticker" with a percentage.
That is the wrong shape for fixing anything: "CapEx 40%" does not say which
years to go and re-extract, so acting on it means opening the CSV by hand.
This emits one record per ticker+field carrying the exact periods that are
empty, so a fix run can be driven straight off the output.

One row per ticker+field rather than per cell: the corpus holds 932 missing
core-8 cells across only 170 ticker+field pairs, and the `periods` list keeps
the full detail without 932 rows of it.

The `absent_column` flag separates the two kinds of gap, which want different
fixes. A column the CSV never declared means the extractor has no pattern for
that metric at all; a declared column that is merely empty for some years
usually means the filings for those years read differently.

Headers resolve through the same alias + unit-suffix logic the rest of the
repo uses, so a CSV spelling EPS as `EPSDiluted` or revenue as
`Revenue_RMB_Mn` is not reported missing -- flagging those would send someone
re-extracting a file that is already correct.

Usage:
  missing_fields.py                          # JSONL to stdout, core-8 only
  missing_fields.py --scope all --format csv
  missing_fields.py --ticker PINS --ticker V
  missing_fields.py --field CapEx --out state/gaps.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from typing import Any, TextIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import integrity_report as ir
import schema

REPO = pathlib.Path(__file__).resolve().parents[1]

# Columns that are bookkeeping, not metrics: never reportable as a gap.
SKIP = {"period", "units", "currency"}

FIELDS = {
    "core8": [ir.DISPLAY[n] for n in ir.CORE8],
    "all": [h for n, _, h in schema.CORE_COLUMNS if n not in SKIP],
}


def scan(root: pathlib.Path | str, *, scope: str = "core8",
         tickers: set[str] | None = None,
         fields: set[str] | None = None) -> list[dict[str, Any]]:
    """One gap record per ticker+field, worst first."""
    root = pathlib.Path(root)
    research = root / "research"
    if not research.is_dir():
        return []

    wanted = [f for f in FIELDS[scope] if not fields or f in fields]
    out: list[dict[str, Any]] = []

    for d in sorted(p for p in research.iterdir() if p.is_dir()):
        ticker = d.name
        if tickers and ticker not in tickers:
            continue
        path = d / "Reports" / f"{ticker}_Metrics.csv"
        if not path.is_file():
            continue
        rows = ir.read_csv_rows(path)
        if not rows:
            continue

        # Which source headers carry each core column. A metric may appear
        # under several spellings; any of them counts as present.
        by_field: dict[str, list[str]] = {}
        for h in rows[0]:
            name = ir.core_field(h) if h else None
            if name:
                by_field.setdefault(ir.DISPLAY.get(name, name), []).append(h)

        fy = [r for r in rows if ir.is_fy(r.get("Period") or "")]
        if not fy:
            continue

        for field in wanted:
            headers = by_field.get(field, [])
            missing = [
                (r.get("Period") or "").strip() for r in fy
                if not any((r.get(h) or "").strip() for h in headers)
            ]
            if not missing:
                continue
            out.append({
                "ticker": ticker,
                "exchange": ir.exchange_of(ticker),
                "field": field,
                "absent_column": not headers,
                "missing_count": len(missing),
                "fy_years": len(fy),
                "periods": missing,
            })

    # Worst first: the biggest gaps are where a fix pays off most.
    out.sort(key=lambda r: (-r["missing_count"], r["ticker"], r["field"]))
    return out


def write_jsonl(rows: list[dict[str, Any]], fh: TextIO) -> None:
    fh.writelines(json.dumps(r) + "\n" for r in rows)


def write_json(rows: list[dict[str, Any]], fh: TextIO) -> None:
    json.dump(rows, fh, indent=1)


def write_csv(rows: list[dict[str, Any]], fh: TextIO) -> None:
    cols = ["ticker", "exchange", "field", "absent_column",
            "missing_count", "fy_years", "periods"]
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    for r in rows:
        # Space-separated so the cell stays one CSV field without quoting.
        w.writerow({**r, "periods": " ".join(r["periods"])})


WRITERS = {"jsonl": write_jsonl, "json": write_json, "csv": write_csv}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(REPO))
    p.add_argument("--scope", choices=sorted(FIELDS), default="core8",
                   help="core8 (DCF-critical, default) or all schema columns")
    p.add_argument("--format", choices=sorted(WRITERS), default="jsonl")
    p.add_argument("--ticker", action="append", dest="tickers", default=None,
                   help="repeatable; default is every ticker")
    p.add_argument("--field", action="append", dest="fields", default=None,
                   help="repeatable; default is every field in scope")
    p.add_argument("--out", default=None, help="default: stdout")
    args = p.parse_args()

    root = pathlib.Path(args.root)
    if not (root / "research").is_dir():
        print(f"missing_fields: no research/ directory under {root}",
              file=sys.stderr)
        return 1

    rows = scan(root, scope=args.scope,
                tickers=set(args.tickers) if args.tickers else None,
                fields=set(args.fields) if args.fields else None)

    writer = WRITERS[args.format]
    if args.out:
        dest = pathlib.Path(args.out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", newline="") as fh:
            writer(rows, fh)
        cells = sum(r["missing_count"] for r in rows)
        print(f"{len(rows)} ticker+field gap(s), {cells} missing cell(s) "
              f"-> {dest}", file=sys.stderr)
    else:
        writer(rows, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
