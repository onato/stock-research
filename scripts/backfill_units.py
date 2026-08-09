#!/usr/bin/env python3
"""Infer the missing `core_metrics.units` from independently-authored figures.

58 of 80 tickers carry NULL units on every row. `metrics_normalized` resolves
unknown units to NULL rather than assuming a scale, so those tickers produce
no comparable data at all and vanish from any cross-ticker screen.

**How the scale is recovered.** Each `{TICKER}_DCF.json` states several of the
same quantities the DB holds -- total debt, last FCF, share count, cash -- and
the analyst wrote them independently, in millions. The ratio between the DB
value and the DCF value therefore lands on a power of ten that names the DB's
scale. Two anchors agreeing is evidence; one is a coincidence.

**What this deliberately does NOT do.**

  * *No magnitude heuristics.* "Revenue of 411,000 looks like thousands" is
    exactly the reasoning that read SEK.NZ as NZ$411bn (see schema.py). A
    number's size cannot establish its unit.
  * *No `facts.units_hint`.* The hint records the scale printed on the filing
    page, but the financial-parser agent already rescaled the value before
    writing core_metrics -- so the hint describes the input, not the stored
    number. Measured: 2CC.NZ's facts say `thousands` 1758 times while its
    revenue rows are plainly millions (39.8 for a company doing ~$80m/yr);
    AGL.NZ, SUM.NZ and OCA.NZ are the same. Trusting it injects 1000x errors.
  * *No fallback.* No consensus leaves units NULL and the ticker unscreenable,
    which is visible. A wrong scale is not.

Usage:
  backfill_units.py                 # dry-run report over every NULL-units DB
  backfill_units.py --apply         # write the resolved ones
  backfill_units.py --ticker EBO.NZ SUM.NZ [--apply]
"""

import argparse
import math
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import fundamentals

REPO = pathlib.Path(__file__).resolve().parents[1]

# DB value / DCF value lands on one of these. The DCF is authored in millions,
# so `millions` is the identity.
LADDER: dict[str, float] = {
    "millions": 1.0,
    "thousands": 1e3,
    "billions": 1e-3,
    "absolute": 1e6,
}

# (DCF inputs key, core_metrics column). Both sides name the same quantity;
# `cash` and `cash_and_equivalents` are alternate spellings in the DCFs.
ANCHORS: list[tuple[str, str]] = [
    ("total_debt", "total_debt"),
    ("last_fcf", "free_cash_flow"),
    ("shares_outstanding", "shares_outstanding"),
    ("cash_and_equivalents", "cash_and_equivalents"),
    ("cash", "cash_and_equivalents"),
]

# log10 tolerance: ~4.7%. Tight enough that only a true decade match votes,
# loose enough to absorb rounding between the DB and a hand-written DCF.
TOL_DEX = 0.02

MIN_ANCHORS = 2


def _anchorable(value: Any) -> float | None:
    """A number usable as a ratio anchor -- so never zero.

    Stricter than fundamentals._num on purpose: zero is a legitimate metric
    value (HLG.NZ genuinely carries no debt) but cannot establish a scale,
    since every ratio against it is undefined.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return None if v == 0 else v


def classify(db_value: Any, dcf_value: Any) -> str | None:
    """Name the DB's scale from one DB/DCF pair, or None if no decade fits."""
    db = _anchorable(db_value)
    dcf = _anchorable(dcf_value)
    if db is None or dcf is None:
        return None
    # A DB in thousands states 1000x the DCF's millions figure, so the ratio
    # is the ladder factor itself -- not its reciprocal.
    ratio = abs(db / dcf)
    for name, factor in LADDER.items():
        if abs(math.log10(ratio / factor)) < TOL_DEX:
            return name
    return None


def anchor_votes(rows: list[dict[str, Any]],
                 inputs: dict[str, Any]) -> dict[str, str]:
    """{anchor key: scale} for every DCF input that matches some DB row.

    Each anchor votes at most once. A ticker's rows span many periods and the
    DCF quotes the latest, so any row matching a decade is taken as the match.
    """
    votes: dict[str, str] = {}
    for json_key, column in ANCHORS:
        if _anchorable(inputs.get(json_key)) is None:
            continue
        for row in rows:
            scale = classify(row.get(column), inputs.get(json_key))
            if scale:
                votes[json_key] = scale
                break
    return votes


def infer(ticker: str, rows: list[dict[str, Any]],
          dcf: dict[str, Any] | None) -> tuple[str | None, str]:
    """(units, rationale). units is None whenever the evidence is not decisive."""
    inputs = (dcf or {}).get("inputs") or {}
    if not inputs:
        return None, "no-dcf-inputs"

    votes = anchor_votes(rows, inputs)
    if not votes:
        return None, "no-anchor-match"

    distinct = set(votes.values())
    if len(distinct) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(votes.items()))
        return None, f"conflicting-anchors ({detail})"
    if len(votes) < MIN_ANCHORS:
        only = next(iter(votes))
        return None, f"insufficient-anchors (only {only})"

    scale = distinct.pop()
    return scale, f"{len(votes)} anchors agree: " + ", ".join(sorted(votes))


def _count_null_units(con: Any) -> int:
    row = con.execute(
        "SELECT count(*) FROM core_metrics WHERE units IS NULL").fetchone()
    return int(row[0]) if row else 0


def apply_units(con: Any, units: str, dry_run: bool = True) -> int:
    """Set `units` on rows that have none. Returns the row count affected.

    Only NULL rows are touched, so re-running is a no-op and an existing
    value -- however it got there -- is never overwritten.
    """
    pending = _count_null_units(con)
    if dry_run or not pending:
        return 0
    con.execute("UPDATE core_metrics SET units = ? WHERE units IS NULL", [units])
    return pending


def _rows(con: Any) -> list[dict[str, Any]]:
    cols = ["total_debt", "free_cash_flow", "shares_outstanding",
            "cash_and_equivalents"]
    res = con.execute(f"SELECT {', '.join(cols)} FROM core_metrics").fetchall()
    return [dict(zip(cols, r, strict=True)) for r in res]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the resolved units (default is a dry run)")
    ap.add_argument("--ticker", nargs="*", default=[])
    ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)

    import duckdb

    repo = pathlib.Path(args.root) if args.root else REPO
    wanted = set(args.ticker)

    resolved: list[tuple[str, str, int]] = []
    refused: list[tuple[str, str]] = []

    for db in sorted(repo.glob("research/*/Reports/*.duckdb")):
        ticker = db.parent.parent.name
        if wanted and ticker not in wanted:
            continue
        con = duckdb.connect(str(db), read_only=not args.apply)
        try:
            null_rows = _count_null_units(con)
            if not null_rows:
                continue
            units, why = infer(ticker, _rows(con), fundamentals.load_dcf(repo, ticker))
            if units is None:
                refused.append((ticker, why))
                continue
            apply_units(con, units, dry_run=not args.apply)
            resolved.append((ticker, f"{units} -- {why}", null_rows))
        finally:
            con.close()

    verb = "SET" if args.apply else "would set"
    print(f"\n  RESOLVED ({len(resolved)}) -- {verb} units\n")
    for ticker, why, n in resolved:
        print(f"    {ticker:10s} {n:4d} rows  {why}")
    print(f"\n  REFUSED ({len(refused)}) -- units stay NULL, ticker unscreenable\n")
    for ticker, why in refused:
        print(f"    {ticker:10s} {why}")
    if not args.apply:
        print("\n  Dry run. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
