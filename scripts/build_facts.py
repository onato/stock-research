#!/usr/bin/env python3
"""Deterministic metric extraction: text filings -> DuckDB facts table.

Replaces the grep/read loop that dominated cost. Measured on AFC.NZ, the
financial-parser subagent spent 183 turns and 18.2M cache-read tokens
re-reading 18 filings (644k tokens of source) 3-6 times each, repeating
one grep pattern 17 times -- a 28x amplification.

This does that search ONCE, linearly, with no model. The agent then
queries the result and spends its turns on judgment instead of hunting.

DELIBERATE NON-GOALS. This script must never:
  * scale units (a wrong thousands/millions call is a 1000x error)
  * pick between competing candidates
  * infer a period the filename does not state
Every candidate is emitted with context; adjudication is the agent's job.
That division is the whole point -- financial-parser.md's rules (the DUOL
SBC double-count, authorization-vs-actual buybacks) encode judgment that
regexes cannot replicate.

The parsing itself lives in scripts/parsers/: exchange-independent
vocabulary in parsers/common.py, the scan driver and generic fallback in
parsers/base.py, and one module per exchange registered by listing suffix.
This file is the CLI and the stable import surface (scan_file, PATTERNS).

Usage: build_facts.py TICKER [--show]
"""

import pathlib
import re
import sys
from collections.abc import Iterator
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import contextlib

import schema
from parsers import get_parser
from parsers.base import BaseParser
from parsers.common import (  # noqa: F401  (re-exported API)
    COMPILED,
    LABEL_RE,
    PATTERNS,
    expected_span,
    fiscal_year_end,
    normalize_label,
    parse_num,
    period_from_filename,
    period_from_text,
    split_lines,
)

REPO = pathlib.Path(__file__).resolve().parents[1]


def segments(line: str) -> Iterator[tuple[str, list[float]]]:
    """Generic line segmentation (compat re-export; see BaseParser.segments)."""
    return BaseParser().segments(line)


def ticker_for(path: pathlib.Path) -> str:
    """The ticker a filing belongs to, for parser routing.

    research/{TICKER}/Extracted/x.txt names the ticker in the directory;
    anywhere else (tests, ad-hoc files) fall back to the {TICKER}_... prefix
    of the filename convention.
    """
    if path.parent.name == "Extracted":
        return path.parent.parent.name
    return path.name.split("_", 1)[0]


def scan_file(path: pathlib.Path,
              fy_end_month: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield candidate facts from one extracted filing."""
    parser = get_parser(ticker_for(path))
    yield from parser.scan(path.read_text(errors="replace"), path.name, fy_end_month)


def folder_fiscal_year_end(files: list[pathlib.Path]) -> int | None:
    """The fiscal-year-end month of the newest annual report, so interims
    can be labelled by fiscal year (a June year-end's December half is H1)."""
    latest: tuple[str, int] | None = None
    for f in files:
        if not re.search(r"_(Annual|10K|20F)_", f.name, re.IGNORECASE):
            continue
        lines = split_lines(f.read_text(errors="replace"))
        month = fiscal_year_end(lines)
        stated = period_from_text(lines, expected_span=12) or ""
        # Year ends change (VUL.AX June -> December): the newest report rules.
        if month and (latest is None or stated > latest[0]):
            latest = (stated, month)
    return latest[1] if latest else None


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("usage: build_facts.py TICKER [--show]", file=sys.stderr)
        return 2
    ticker = args[0]

    extracted = REPO / "research" / ticker / "Extracted"
    if not extracted.is_dir():
        print(f"no Extracted/ for {ticker} -- run pdftotext first", file=sys.stderr)
        return 1

    files = sorted(extracted.glob("*.txt"))
    facts: list[dict[str, Any]] = []
    fy_end = folder_fiscal_year_end(files)
    for f in files:
        facts.extend(scan_file(f, fy_end))

    import duckdb
    db = REPO / "research" / ticker / "Reports" / f"{ticker}.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    schema.ensure_schema(con)
    # Migration shim: schema.py now declares currency in the facts DDL, but
    # DBs created before that lack the column. Remove once every ticker DB
    # has been rebuilt at least once.
    with contextlib.suppress(Exception):
        con.execute("ALTER TABLE facts ADD COLUMN currency TEXT")
    con.execute("DELETE FROM facts")
    if facts:
        con.executemany(
            "INSERT INTO facts (metric, period, value_raw, units_hint, source_file,"
            " line_no, context, confidence, currency)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [[f[k] for k in ("metric", "period", "value_raw", "units_hint",
                             "source_file", "line_no", "context", "confidence",
                             "currency")] for f in facts],
        )
    summary = con.execute(
        "SELECT metric, count(*), count(DISTINCT period) FROM facts"
        " GROUP BY metric ORDER BY 2 DESC").fetchall()
    hints = con.execute(
        "SELECT DISTINCT units_hint FROM facts WHERE units_hint IS NOT NULL").fetchall()
    con.close()

    print(f"{ticker}: {len(facts)} candidates from {len(files)} filings -> {db.name}")
    print(f"  units hint: {[h[0] for h in hints] or 'NONE FOUND (agent must determine)'}")
    if "--show" in sys.argv:
        print(f"\n  {'metric':22s} {'rows':>5s} {'periods':>8s}")
        for metric, n, p in summary:
            print(f"    {metric:20s} {n:5d} {p:8d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
