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
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import schema  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]


def sort_key(period):
    """Chronological, with half/quarter periods following their year.

    Dashboards plot in row order, so 'oldest first' matters.
    """
    p = (period or "").strip().upper()
    year, sub = 0, 0
    for token in p.replace("-", " ").split():
        if token.isdigit() and len(token) == 4:
            year = int(token)
        elif token.startswith("FY") and token[2:].isdigit():
            year, sub = int(token[2:]), 9      # full year sorts after its parts
        elif token.startswith("H") and token[1:].isdigit():
            sub = int(token[1:]) * 2
        elif token.startswith("Q") and token[1:].isdigit():
            sub = int(token[1:])
    return (year, sub, p)


def main():
    if len(sys.argv) < 2:
        print("usage: export_csv.py TICKER", file=sys.stderr)
        return 2
    ticker = sys.argv[1]

    db = REPO / ticker / "Reports" / f"{ticker}.duckdb"
    out = REPO / ticker / "Reports" / f"{ticker}_Metrics.csv"
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
