#!/usr/bin/env python3
"""Rewrite Metrics CSVs onto the canonical header.

62 distinct header shapes and 362 distinct column names accumulated across the
80 committed CSVs -- the same metric spelled `EPS`, `EPSDiluted`,
`EPS_Diluted`, `Revenue_RMB_Mn`. Cross-ticker work then has to guess, which is
what `schema.py` exists to stop. `export_csv.py` has always written the
canonical 24 columns, so the drift lives in files that predate it or were
written by hand; this brings them into line.

Two rules, both load-bearing:

*Nothing numeric is lost.* A header that does not resolve to a core metric is
a company KPI (iPhoneRevenue, ARR, MAU) and is preserved, appended after the
core block. It cannot move to a separate file: the generated dashboards embed
this CSV inline and chart those columns **by name**, so relocating them breaks
the charts silently.

*Collisions resolve to the conservative figure.* AAPL, ASML, ADYEY and SFM all
carry EPSBasic and EPSDiluted, and `schema.ALIASES` maps both to `eps`.
Diluted wins -- it is the figure a DCF should use -- and the loser is kept as a
KPI column rather than dropped.

Usage:
  normalize_csv.py --check [TICKER ...]   # report, write nothing
  normalize_csv.py --write [TICKER ...]   # rewrite in place
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import integrity_report
import schema

REPO = pathlib.Path(__file__).resolve().parents[1]

# When two headers claim the same core column, prefer the one whose lowercased
# name contains the earliest-listed token. Diluted per-share figures are the
# conservative choice; "total"/"net" beat segment-ish spellings of revenue.
PREFERENCE: list[str] = ["diluted", "total", "net", "adjusted"]

# core column name -> the CamelCase header the CSVs and dashboards use.
CSV_HEADER_FOR: dict[str, str] = dict(
    zip(schema.CORE_NAMES, schema.CSV_HEADERS, strict=True))


def target_column(header: str) -> str | None:
    """Map a source header to its core column, bookkeeping columns included.

    integrity_report.core_field() deliberately drops period/units/currency --
    they are not metrics and must not count toward a fill rate. The rewrite
    does need them placed, so this resolves them too, reusing the same
    unit-suffix retry (Revenue_RMB_Mn, EPS_cents).
    """
    name = schema.normalize(header)
    if name is None:
        name = integrity_report.strip_units(header)
    return name


def _rank(header: str, populated: int) -> tuple[int, int, int]:
    """Sort key for competing headers: populated first, then preference.

    A column with values always beats an empty one -- an alias that exists but
    was never filled in must not shadow the column that carries the data.
    """
    low = header.lower()
    pref = next((i for i, tok in enumerate(PREFERENCE) if tok in low),
                len(PREFERENCE))
    return (0 if populated else 1, pref, len(header))


def _read(path: pathlib.Path) -> tuple[list[str], list[list[str]]]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    except (OSError, UnicodeDecodeError, csv.Error):
        return [], []
    if not rows:
        return [], []
    return rows[0], rows[1:]


def plan_file(path: pathlib.Path) -> dict[str, Any]:
    """Work out the rewrite without touching the file."""
    header, rows = _read(path)
    plan: dict[str, Any] = {
        "path": str(path), "would_change": False,
        "renames": [], "kpis": [], "dropped": [], "collisions": [],
    }
    if not header:
        return plan

    # How many non-empty values each column holds, so an empty alias never
    # shadows the populated column it competes with.
    counts = {
        i: sum(1 for r in rows if i < len(r) and (r[i] or "").strip())
        for i in range(len(header))
    }

    # Group source columns by the core column they claim.
    claims: dict[str, list[int]] = {}
    kpi_idx: list[int] = []
    for i, h in enumerate(header):
        if not h:
            continue
        name = target_column(h)
        if name:
            claims.setdefault(name, []).append(i)
        else:
            kpi_idx.append(i)

    winners: dict[str, int] = {}
    renamed_losers: dict[int, str] = {}
    for name, idxs in claims.items():
        idxs.sort(key=lambda i: _rank(header[i], counts[i]))
        winners[name] = idxs[0]
        for loser in idxs[1:]:
            # Keep the losing column's data rather than discarding it. It
            # needs a name that cannot collide with a canonical header --
            # a loser literally called "Revenue" would otherwise appear
            # twice, and every by-name reader would take the wrong one.
            label = header[loser]
            if label in schema.CSV_HEADERS:
                label = f"{label}_alt"
            renamed_losers[loser] = label
            kpi_idx.append(loser)
            plan["collisions"].append((header[loser], header[idxs[0]], name))

    for name, i in winners.items():
        csv_header = CSV_HEADER_FOR[name]
        if header[i] != csv_header:
            plan["renames"].append((header[i], csv_header))

    # Some files carry values past the end of their own header: 36 of META's
    # rows have 26 fields against 25 columns, and NVDA/PANW/FIG do the same.
    # Indexing by header position alone would drop those silently, so any
    # overflow position gets a synthetic column and keeps its data.
    widest = max((len(r) for r in rows), default=0)
    extra_idx = list(range(len(header), widest))
    kpi_idx.extend(extra_idx)

    plan["kpis"] = [
        renamed_losers.get(i, header[i] if i < len(header)
                           else f"Extra{i - len(header) + 1}")
        for i in sorted(kpi_idx)
    ]
    plan["winners"] = winners
    plan["kpi_idx"] = sorted(kpi_idx)
    plan["orphan_values"] = len(extra_idx)

    target = schema.CSV_HEADERS + plan["kpis"]
    plan["would_change"] = header != target
    plan["target_header"] = target
    return plan


def normalize_file(path: pathlib.Path) -> bool:
    """Rewrite `path` onto the canonical header. True if the file changed."""
    plan = plan_file(path)
    if not plan["would_change"]:
        return False

    _, rows = _read(path)
    winners: dict[str, int] = plan["winners"]
    kpi_idx: list[int] = plan["kpi_idx"]

    def cell(row: list[str], i: int) -> str:
        # Rows are occasionally ragged (a trailing column omitted); treat a
        # short row as empty in the missing positions rather than failing.
        return row[i] if i < len(row) else ""

    out_rows = []
    for row in rows:
        if not any((c or "").strip() for c in row):
            continue
        core = [cell(row, winners[n]) if n in winners else ""
                for n in schema.CORE_NAMES]
        out_rows.append(core + [cell(row, i) for i in kpi_idx])

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(plan["target_header"])
        w.writerows(out_rows)
    return True


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
                      help="report what would change, write nothing (default)")
    mode.add_argument("--write", action="store_true",
                      help="rewrite the files in place")
    p.add_argument("--verbose", action="store_true",
                   help="list every rename, not just a per-file summary")
    args = p.parse_args()

    paths = [q for q in discover(args.tickers) if q.is_file()]
    if not paths:
        print("no Metrics CSVs found", file=sys.stderr)
        return 1

    changed = skipped = 0
    collisions: list[tuple[str, str, str, str]] = []
    for path in paths:
        ticker = path.parent.parent.name
        plan = plan_file(path)
        if not plan["would_change"]:
            skipped += 1
            continue
        changed += 1
        for loser, winner, name in plan["collisions"]:
            collisions.append((ticker, loser, winner, name))
        if args.write:
            normalize_file(path)
        if args.verbose or not args.write:
            renames = ", ".join(f"{a}->{b}" for a, b in plan["renames"][:6])
            print(f"  {ticker:12} {len(plan['renames']):2} renames, "
                  f"{len(plan['kpis']):2} kpis  {renames}")

    verb = "rewrote" if args.write else "would rewrite"
    print(f"\n{verb} {changed} file(s); {skipped} already canonical")
    if collisions:
        print(f"\n{len(collisions)} collision(s) -- loser kept as a KPI column:")
        for ticker, loser, winner, name in collisions:
            print(f"  {ticker:12} {loser} lost to {winner} for {name}")
    if not args.write:
        print("\n(--check mode: nothing written. Re-run with --write to apply.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
