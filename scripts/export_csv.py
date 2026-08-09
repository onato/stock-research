#!/usr/bin/env python3
"""Export core_metrics to the CSV shape the dashboards already read.

The DB uses snake_case column names; every existing dashboard embeds CSV
with CamelCase headers (`Period,Revenue,GrossProfit,...`). schema.py holds
both spellings -- this applies the mapping so the agent does not have to
hand-write 24 column aliases in a COPY statement (it got that wrong, which
is what this script exists to prevent).

Usage: export_csv.py TICKER
"""

import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import periods
import schema

REPO = pathlib.Path(__file__).resolve().parents[1]


def sort_key(period: str | None) -> tuple[int, int, str]:
    """Chronological, with half/quarter periods preceding their full year.

    Dashboards plot in row order, so 'oldest first' matters.

    Delegates to periods.py rather than re-deriving the grammar. The previous
    implementation looped over tokens and let a trailing `FY2024` overwrite
    the sub-rank the leading `Q1` had already set, so `Q1 FY2024` tied with
    `FY2024` -- which is how 21 committed CSVs came to list a full year ahead
    of the quarters it contains.
    """
    return periods.sort_key(period)


def _would_lose(out: pathlib.Path,
                rows: list[tuple[object, ...]]) -> tuple[list[str], list[tuple[str, str]]]:
    """What an export would destroy: (periods dropped, cells blanked).

    Both matter. The period check catches a table that never received the
    CSV's H1/Q rows; the cell check catches columns the CSV carries that
    nothing ever adjudicated -- a case the row count cannot see, because the
    row count goes UP while the data goes away.
    """
    try:
        with open(out, newline="", errors="replace") as fh:
            existing = list(csv.DictReader(fh))
    except OSError:
        return [], []

    idx = schema.CORE_NAMES.index("period")
    exported = {str(r[idx]): r for r in rows}
    lost_periods = [str(r.get("Period") or "") for r in existing
                    if str(r.get("Period") or "") not in exported]

    lost_cells: list[tuple[str, str]] = []
    header_to_col = dict(zip(schema.CSV_HEADERS, schema.CORE_NAMES, strict=True))
    for row in existing:
        period = str(row.get("Period") or "")
        target = exported.get(period)
        if target is None:
            continue                      # already counted as a lost period
        for header, value in row.items():
            if not (value or "").strip():
                continue
            col = header_to_col.get(header)
            if col is None:
                # A column the canonical schema does not carry at all; the
                # export drops it wholesale.
                lost_cells.append((period, header))
            elif target[schema.CORE_NAMES.index(col)] is None:
                lost_cells.append((period, header))
    return lost_periods, lost_cells


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: export_csv.py TICKER [--force]", file=sys.stderr)
        return 2
    ticker = sys.argv[1]
    force = "--force" in sys.argv

    db = REPO / "research" / ticker / "Reports" / f"{ticker}.duckdb"
    out = REPO / "research" / ticker / "Reports" / f"{ticker}_Metrics.csv"
    if not db.exists():
        print(f"no database at {db}", file=sys.stderr)
        return 1

    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    cols = ", ".join(schema.CORE_NAMES)
    rows = con.execute(f"SELECT {cols} FROM core_metrics").fetchall()
    con.close()

    if not rows:
        print(f"core_metrics is empty for {ticker} -- nothing exported", file=sys.stderr)
        return 1

    idx = schema.CORE_NAMES.index("period")
    rows.sort(key=lambda r: sort_key(r[idx]))

    # Refuse to shrink an existing CSV. WISE.L's agent wrote 18 periods
    # (H1 + FY) to the CSV but only 5 annual ones to core_metrics; running
    # the export then silently replaced real data with a subset. A CSV
    # with more periods than the table means the table is incomplete, not
    # that the CSV is stale.
    if out.exists() and not force:
        lost_periods, lost_cells = _would_lose(out, rows)
        if lost_periods:
            print(f"REFUSING to overwrite {out.name}: it has "
                  f"{len(rows) + len(lost_periods)} periods but core_metrics "
                  f"has only {len(rows)}.", file=sys.stderr)
            print("  The table is missing periods the CSV already covers "
                  "(often H1/Q rows).", file=sys.stderr)
            print("  Populate core_metrics fully, or re-run with --force to "
                  "discard them.", file=sys.stderr)
            return 1
        # A growing row count is not proof the export is safe. Re-exporting
        # UBER turned a 26-row CSV into 44 while dropping 325 populated cells
        # -- CostOfRevenue, GrossProfit, TotalDebt -- whose columns the CSV
        # carries but nothing ever adjudicated into core_metrics.
        if lost_cells:
            shown = ", ".join(sorted({c for _, c in lost_cells})[:6])
            print(f"REFUSING to overwrite {out.name}: the export would blank "
                  f"{len(lost_cells)} populated cell(s).", file=sys.stderr)
            print(f"  Columns affected: {shown}", file=sys.stderr)
            print("  core_metrics has no value for them, so exporting would "
                  "discard data the CSV already holds.", file=sys.stderr)
            print("  Adjudicate them into the table, or re-run with --force "
                  "to discard them.", file=sys.stderr)
            return 1

    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(schema.CSV_HEADERS)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])

    print(f"{ticker}: {len(rows)} periods -> {out.name} "
          f"({len(schema.CSV_HEADERS)} columns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
