#!/usr/bin/env python3
"""Export core_metrics to the CSV shape the dashboards already read.

The DB uses snake_case column names; every existing dashboard embeds CSV
with CamelCase headers (`Period,Revenue,GrossProfit,...`). schema.py holds
both spellings -- this applies the mapping so the agent does not have to
hand-write 24 column aliases in a COPY statement (it got that wrong, which
is what this script exists to prevent).

The core columns are rebuilt from the table, but any column the schema does
not model (a business KPI like GrossBookings or TPV) is carried through from
the existing CSV. The CSV is the committed system of record and the DB a
gitignored, rebuildable cache, so this export refines its source rather than
replacing it. `--force` opts out and regenerates from the table alone.

Usage: export_csv.py TICKER [--force]
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
                # A column the canonical schema does not model. It is no
                # longer lost -- carry_columns() copies it through -- so it
                # is not counted here. Only --force drops it, which is what
                # --force means.
                continue
            if target[schema.CORE_NAMES.index(col)] is None:
                lost_cells.append((period, header))
    return lost_periods, lost_cells


def carry_columns(out: pathlib.Path, periods: list[str]
                  ) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Non-schema columns from the existing CSV, keyed by period.

    The CSV is the committed system of record and the DB a gitignored,
    rebuildable cache (CLAUDE.md), so the derived artifact must not subtract
    from its source. core_metrics has no column for a business KPI like
    GrossBookings or TPV, and the kpis table is not a reliable substitute:
    of the 58 committed CSVs carrying such columns, UBER and PYPL hold none
    of them in kpis and XYZ's kpis table is empty, so reading from there
    would destroy precisely the figures the dashboards plot.

    Values travel with their period, because the export re-sorts rows
    oldest-first. A period the old CSV did not have gets a blank rather
    than a neighbour's value.
    """
    try:
        with open(out, newline="", errors="replace") as fh:
            existing = list(csv.DictReader(fh))
    except OSError:
        return [], {}
    if not existing:
        return [], {}

    extra = [h for h in existing[0] if h and h not in schema.CSV_HEADERS]
    if not extra:
        return [], {}

    wanted = set(periods)
    by_period = {}
    for row in existing:
        period = str(row.get("Period") or "")
        if period in wanted:
            by_period[period] = {h: (row.get(h) or "") for h in extra}
    return extra, by_period


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

    extra: list[str] = []
    carried: dict[str, dict[str, str]] = {}
    if out.exists() and not force:
        extra, carried = carry_columns(out, [str(r[idx]) for r in rows])

    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(schema.CSV_HEADERS + extra)
        for r in rows:
            values = ["" if v is None else v for v in r]
            if extra:
                held = carried.get(str(r[idx]), {})
                values += [held.get(h, "") for h in extra]
            w.writerow(values)

    note = f", {len(extra)} carried" if extra else ""
    print(f"{ticker}: {len(rows)} periods -> {out.name} "
          f"({len(schema.CSV_HEADERS)} columns{note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
