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

Whitelisted business KPIs are additionally *promoted* out of the `kpis` table
into extra columns (see schema.PROMOTE_KPIS). That table was write-mostly --
154 of 171 ticker DBs fill it and only dcf_context.py ever read it -- so a
metric like ActiveCustomers was extracted, stored and then dropped, because a
dashboard chart can only name a CSV header. Promotion is a one-way ratchet
from the cache into the system of record: it only ever ADDS. A carried value
always wins over a table value, so nothing already committed can be blanked.

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


def promoted_columns(db: pathlib.Path, row_periods: list[str]
                     ) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Whitelisted business KPIs from the `kpis` table, keyed by period.

    Same return shape as `carry_columns` so the writer merges both uniformly.
    Only names in `schema.PROMOTE_KPIS` come through: the table is ~35% owner-FCF
    components (InterestIncome on 67 tickers, CashTaxesPaid on 58), and those
    belong to the DCF via dcf_context.py, not to a cross-ticker CSV.

    Periods are matched on their canonical form, because `kpis` and
    `core_metrics` may spell the same six months `H1 FY2020` and `H1-2020`.
    periods.py is the single parser -- see CLAUDE.md.

    Two rows that disagree about one (period, metric) yield NO value plus a
    warning. Silently picking one is the SEK.NZ class of bug: a missing cell
    is obvious, a plausible wrong one is not.
    """
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute("SELECT period, name, value FROM kpis").fetchall()
    except duckdb.Error:
        return [], {}          # a legacy DB born before the kpis table
    finally:
        con.close()

    canon_to_period = {periods.canonical(periods.parse(p)): p for p in row_periods}
    seen: dict[tuple[str, str], float | None] = {}
    conflicts: set[tuple[str, str]] = set()
    for raw_period, name, value in rows:
        header = schema.promote_header(name)
        if header is None or value is None:
            continue
        period = canon_to_period.get(periods.canonical(periods.parse(raw_period)))
        if period is None:
            continue
        key = (period, header)
        if key in seen and seen[key] != value:
            conflicts.add(key)
        seen[key] = value

    for period, header in sorted(conflicts):
        print(f"WARNING: kpis has disagreeing values for {header} in {period}"
              " -- leaving the cell empty", file=sys.stderr)
        del seen[(period, header)]

    by_period: dict[str, dict[str, str]] = {}
    for (period, header), value in seen.items():
        by_period.setdefault(period, {})[header] = str(value)
    headers = sorted({h for _, h in seen})
    return headers, by_period


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

    row_periods = [str(r[idx]) for r in rows]
    extra: list[str] = []
    carried: dict[str, dict[str, str]] = {}
    if out.exists() and not force:
        extra, carried = carry_columns(out, row_periods)

    # Promotion runs under --force too. --force means "rebuild from the table",
    # and a promoted column IS table-backed, so dropping it would make the one
    # flag that regenerates from the DB the one flag that loses the DB's own
    # KPIs. Purely-carried columns still go, which is what --force is for.
    promoted_names, promoted = promoted_columns(db, row_periods)
    extra += [h for h in promoted_names if h not in extra]

    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(schema.CSV_HEADERS + extra)
        for r in rows:
            values = ["" if v is None else v for v in r]
            if extra:
                held = carried.get(str(r[idx]), {})
                gained = promoted.get(str(r[idx]), {})
                # The CSV is the system of record: a populated carried cell
                # always wins, and a promoted value only fills a blank.
                values += [held.get(h) or gained.get(h, "") for h in extra]
            w.writerow(values)

    note = f", {len(extra)} carried" if extra else ""
    if promoted_names:
        note += f", {len(promoted_names)} promoted from kpis"
    print(f"{ticker}: {len(rows)} periods -> {out.name} "
          f"({len(schema.CSV_HEADERS)} columns{note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
