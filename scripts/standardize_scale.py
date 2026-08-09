#!/usr/bin/env python3
"""Put every metrics CSV on one scale, and make that scale explicit.

Only 21 of 81 committed CSVs record `Units` and 22 record `Currency`. The rest
are silent, and a number cannot reveal its own scale: AAPL's revenue of
416,161 is millions while 0285.HK's 179,477 is thousands, and nothing about
the magnitudes distinguishes them. Every consumer therefore has to guess,
which is the failure `schema.py` documents -- SEK.NZ once read as NZ$411bn of
revenue for a company making ~NZ$400m.

**How the scale is recovered.** The same way `backfill_units.py` does it for
the DuckDBs, but against the CSVs, which are the system of record: each
`{TICKER}_DCF.json` states several of the same quantities in millions,
authored independently, so the ratio between the CSV value and the DCF value
lands on a power of ten that names the CSV's scale. Two agreeing anchors are
evidence; one is a coincidence; none is a refusal. This module reuses that
module's ladder, anchors and tolerance rather than inventing a second
vocabulary for the same idea.

**What this deliberately does not do.**

  * *No magnitude heuristics.* "Revenue of 411,000 looks like thousands" is
    exactly the reasoning that produced the SEK.NZ error.
  * *No default.* An unresolved ticker keeps a blank `Units` and is reported.
    A missing unit is obvious; a wrong one is not.
  * *No rescaling of per-share or percentage columns.* AIA.NZ's EPS column
    holds 0.37 (dollars) and 25.87 (cents) because something rescaled a
    per-share figure once already.
  * *No currency invention.* A DCF that says "NZ$" or "AUD (fundamentals) /
    NZD (reported valuation outputs)" is not stating a code, and choosing one
    would be a guess about which figures the CSV holds.

Usage:
  standardize_scale.py --check              # report, write nothing (default)
  standardize_scale.py --write [TICKER ...]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import re
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import backfill_units as bu
import schema

REPO = pathlib.Path(__file__).resolve().parents[1]

# The scale every CSV is normalised to (CLAUDE.md's cross-ticker convention).
TARGET = "millions"

# Columns whose values are scale-free: per-share amounts and percentages.
# Rescaling one of these is a silent corruption, not a units change.
SCALE_FREE = {"period", "units", "currency", "eps", "dividend_per_share",
              "gross_margin", "operating_margin", "net_margin"}

CURRENCY_CODE = re.compile(r"^[A-Za-z]{3}$")

# Codes appearing across the corpus, used only to spot a currency embedded in
# a units string ("usd millions"). Not a validation list -- an unlisted code
# from a DCF's own `currency` field is still taken at face value.
CURRENCIES = {"USD", "NZD", "AUD", "EUR", "GBP", "HKD", "CNY", "RMB", "JPY",
              "SGD", "TWD", "CAD", "CHF", "SEK", "NOK", "DKK", "INR", "KRW"}

# DCF anchor key -> the CSV header holding the same quantity. Mirrors
# backfill_units.ANCHORS, translated from core column names to CSV headers.
HEADER_FOR = {name: header for name, _, header in schema.CORE_COLUMNS}


def scalable_headers(header: list[str]) -> list[str]:
    """Headers whose values move with the unit scale."""
    out = []
    for h in header:
        if not h:
            continue
        name = schema.normalize(h)
        if name is None or name in SCALE_FREE:
            continue
        out.append(h)
    return out


def read_rows(path: pathlib.Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return list(reader.fieldnames or []), list(reader)
    except (OSError, UnicodeDecodeError, csv.Error):
        return [], []


def _num(text: str | None) -> float | None:
    try:
        return float((text or "").strip())
    except (TypeError, ValueError):
        return None


def declared_units(rows: list[dict[str, str]]) -> str | None:
    for row in rows:
        got = (row.get("Units") or "").strip()
        if got:
            return got
    return None


def declared_currency(rows: list[dict[str, str]]) -> str | None:
    for row in rows:
        got = (row.get("Currency") or "").strip()
        if got:
            return got
    return None


def _dcf_inputs(reports: pathlib.Path, ticker: str) -> dict[str, Any]:
    path = reports / f"{ticker}_DCF.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    inputs = data.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _latest(rows: list[dict[str, str]], header: str) -> float | None:
    """Newest populated value for a column, matching how the DCF was built."""
    for row in reversed(rows):
        if not (row.get("Period") or "").strip().upper().startswith("FY"):
            continue
        value = _num(row.get(header))
        if value:            # zero cannot anchor a ratio
            return value
    return None


def parse_units(text: str) -> str | None:
    """A declared units string -> a ladder rung, or None if ambiguous.

    The DCFs state their scale in prose: "millions except per-share figures",
    "usd millions", "absolute dollars (not thousands or millions) except
    shares_outstanding". A single scale word is a declaration; the qualifiers
    name per-share carve-outs, which are the columns SCALE_FREE already
    protects. Two competing scale words are not resolvable and are refused --
    which is why the "not thousands or millions" phrasing is stripped before
    counting.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    # "(not thousands or millions)" disclaims those scales rather than
    # declaring them; dropping the parenthetical avoids a false ambiguity.
    lowered = re.sub(r"\(not[^)]*\)", " ", lowered)

    found = {rung for rung, word in
             (("absolute", "absolute"), ("thousands", "thousand"),
              ("millions", "million"), ("billions", "billion"))
             if word in lowered}
    return found.pop() if len(found) == 1 else None


def resolve_scale(reports: pathlib.Path, ticker: str) -> str | None:
    """The CSV's scale: its own label, else the DCF's, else agreeing anchors."""
    reports = pathlib.Path(reports)
    _, rows = read_rows(reports / f"{ticker}_Metrics.csv")
    if not rows:
        return None
    declared = declared_units(rows)
    if declared:
        return declared

    inputs = _dcf_inputs(reports, ticker)
    if not inputs:
        return None

    # A stated unit is evidence, not inference: prefer it to the ratio work.
    stated = parse_units(str(inputs.get("units") or ""))
    if stated:
        return stated

    votes: dict[str, int] = {}
    for key, column in bu.ANCHORS:
        dcf_value = bu._anchorable(inputs.get(key))
        if dcf_value is None:
            continue
        csv_value = _latest(rows, HEADER_FOR.get(column, column))
        if csv_value is None:
            continue
        # The DCF is authored in millions, so csv/dcf lands on the ladder.
        try:
            dex = math.log10(abs(csv_value) / abs(dcf_value))
        except (ValueError, ZeroDivisionError):
            continue
        for name, factor in bu.LADDER.items():
            if abs(dex - math.log10(factor)) <= bu.TOL_DEX:
                votes[name] = votes.get(name, 0) + 1
                break

    if not votes:
        return None
    best = max(votes, key=lambda k: votes[k])
    if votes[best] < bu.MIN_ANCHORS:
        return None
    # A second scale with its own support means the anchors disagree.
    if any(name != best and count >= bu.MIN_ANCHORS
           for name, count in votes.items()):
        return None
    return best


def resolve_currency(reports: pathlib.Path, ticker: str) -> str | None:
    """The CSV's currency: its own column first, else the DCF's if it is a code."""
    reports = pathlib.Path(reports)
    _, rows = read_rows(reports / f"{ticker}_Metrics.csv")
    existing = declared_currency(rows) if rows else None
    if existing and CURRENCY_CODE.match(existing):
        return existing.upper()
    if existing:
        return None

    inputs = _dcf_inputs(reports, ticker)
    # `currency_fcf` is the spelling BABA-style DCFs use for the same fact.
    for key in ("currency", "currency_fcf"):
        text = str(inputs.get(key) or "").strip()
        if CURRENCY_CODE.match(text):
            return text.upper()

    # A units string may name the currency too ("usd millions").
    for code in re.findall(r"\b([A-Za-z]{3})\b",
                           str(inputs.get("units") or "").upper()):
        if code in CURRENCIES:
            return code
    return None


def convert_file(path: pathlib.Path, target: str = TARGET,
                 source: str | None = None) -> bool:
    """Rescale every money column into `target`. True if the file changed.

    `source` overrides the CSV's own label, for files that never carried one:
    FIG declares nothing while its DCF says thousands, and converting from a
    label that is not there would no-op and then stamp the unconverted values
    "millions" -- writing a 1000x error into the file as fact.
    """
    path = pathlib.Path(path)
    header, rows = read_rows(path)
    if not rows:
        return False
    source = source or declared_units(rows)
    if not source:
        return False

    # LADDER maps a unit to the ratio a value in it bears to the same value in
    # millions (thousands = 1e3, since 6556 thousands is 6.556 millions), so
    # converting divides by the source's rung and multiplies by the target's.
    try:
        factor = bu.LADDER[target] / bu.LADDER[_canonical(source)]
    except KeyError:
        return False
    if factor == 1.0:
        return False

    columns = scalable_headers(header)
    for row in rows:
        for column in columns:
            value = _num(row.get(column))
            if value is None:
                continue          # blank or non-numeric: leave exactly as-is
            row[column] = _fmt(value * factor)
        if (row.get("Units") or "").strip():
            row["Units"] = target

    _write(path, header, rows)
    return True


def _canonical(units: str | None) -> str:
    """Map a declared unit string onto the ladder's vocabulary."""
    text = (units or "").strip().lower()
    if text.startswith("absolute") or text == "units":
        return "absolute"
    return text


def _fmt(value: float) -> str:
    """Render without exponent notation or spurious trailing zeros."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10f}".rstrip("0").rstrip(".")


def label_file(path: pathlib.Path, units: str | None,
               currency: str | None) -> bool:
    """Fill blank Units/Currency on rows that carry data. Never overwrites."""
    path = pathlib.Path(path)
    header, rows = read_rows(path)
    if not rows:
        return False
    if "Units" not in header or "Currency" not in header:
        return False

    data_columns = [h for h in header
                    if h and h not in ("Period", "Units", "Currency")]
    changed = False
    for row in rows:
        # A row with no values is not a period we know anything about;
        # labelling it would invent a fact about nothing.
        if not any((row.get(c) or "").strip() for c in data_columns):
            continue
        if units and not (row.get("Units") or "").strip():
            row["Units"] = units
            changed = True
        if currency and not (row.get("Currency") or "").strip():
            row["Currency"] = currency
            changed = True

    if changed:
        _write(path, header, rows)
    return changed


def _write(path: pathlib.Path, header: list[str],
           rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in header})


def plan(repo: pathlib.Path, tickers: list[str]) -> list[dict[str, Any]]:
    """One record per ticker: current state, resolved scale, what would change."""
    repo = pathlib.Path(repo)
    out: list[dict[str, Any]] = []
    paths = ([repo / "research" / t / "Reports" / f"{t}_Metrics.csv"
              for t in tickers] if tickers
             else sorted(repo.glob("research/*/Reports/*_Metrics.csv")))

    for path in paths:
        if not path.is_file():
            continue
        ticker = path.parent.parent.name
        _, rows = read_rows(path)
        if not rows:
            continue
        units = resolve_scale(path.parent, ticker)
        currency = resolve_currency(path.parent, ticker)
        out.append({
            "ticker": ticker,
            "path": str(path),
            "declared_units": declared_units(rows),
            "declared_currency": declared_currency(rows),
            "resolved_units": units,
            "resolved_currency": currency,
            "needs_conversion": bool(units) and _canonical(units) != TARGET,
            "source_units": units,
        })
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("tickers", nargs="*", help="default: every ticker")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="report what would change (default)")
    mode.add_argument("--write", action="store_true", help="apply")
    args = p.parse_args()

    records = plan(REPO, args.tickers)
    if not records:
        print("no Metrics CSVs found", file=sys.stderr)
        return 1

    converted = labelled = refused = 0
    unresolved: list[str] = []

    for rec in records:
        path = pathlib.Path(rec["path"])
        ticker = rec["ticker"]

        if rec["needs_conversion"]:
            print(f"  {ticker:10} CONVERT {rec['source_units']} -> {TARGET}")
            converted += 1
            if args.write:
                convert_file(path, TARGET, source=rec["source_units"])

        units = TARGET if rec["needs_conversion"] else rec["resolved_units"]
        missing = []
        if not rec["declared_units"]:
            missing.append(f"Units={units or '?'}")
        if not rec["declared_currency"]:
            missing.append(f"Currency={rec['resolved_currency'] or '?'}")

        if missing:
            if units and rec["resolved_currency"]:
                print(f"  {ticker:10} LABEL   {', '.join(missing)}")
                labelled += 1
                if args.write:
                    label_file(path, units, rec["resolved_currency"])
            else:
                refused += 1
                unresolved.append(
                    f"{ticker} ({'no scale' if not units else 'no currency'})")

    verb = "converted" if args.write else "would convert"
    print(f"\n{verb} {converted}, "
          f"{'labelled' if args.write else 'would label'} {labelled}, "
          f"refused {refused}")
    if unresolved:
        print("\nunresolved -- left blank rather than guessed:")
        for line in unresolved:
            print(f"  {line}")
    if not args.write:
        print("\n(--check mode: nothing written. Re-run with --write.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
