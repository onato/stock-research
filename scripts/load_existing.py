#!/usr/bin/env python3
"""Backfill per-ticker DuckDBs from the metrics CSVs already committed.

The shared schema is worth little if it only covers tickers researched from
today onward -- a screener needs the whole history. This maps each existing
CSV's headers onto `core_metrics` via schema.ALIASES and reports what did
not map, rather than silently dropping it.

Usage:
  load_existing.py                # every ticker with a metrics CSV
  load_existing.py AFC.NZ AGL.NZ  # specific tickers
  load_existing.py --report       # show alias coverage, write nothing
"""

import csv
import sys
import pathlib
import collections

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import schema  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]


def parse_number(raw):
    """CSV values carry commas, currency marks, percent signs and blanks."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("$", "").replace("%", "")
    if s in ("", "-", "--", "N/A", "n/a", "NA", "None", "null"):
        return None
    if s.startswith("(") and s.endswith(")"):   # (1,234) = negative
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def read_csv(path):
    """Return (rows, headers, unmapped_headers)."""
    with open(path, newline="", errors="replace") as fh:
        rdr = csv.DictReader(fh)
        headers = rdr.fieldnames or []
        rows = list(rdr)
    unmapped = [h for h in headers if h and schema.normalize(h) is None]
    return rows, headers, unmapped


def to_core(rows):
    """Map CSV rows onto core columns; everything else becomes a KPI row."""
    core, kpis = [], []
    for row in rows:
        rec = {c: None for c in schema.CORE_NAMES}
        for header, raw in row.items():
            if not header:
                continue
            col = schema.normalize(header)
            if col == "period":
                rec["period"] = (raw or "").strip()
            elif col in ("units", "currency"):
                rec[col] = (raw or "").strip() or None
            elif col:
                rec[col] = parse_number(raw)
            else:
                val = parse_number(raw)
                if val is not None:
                    kpis.append((row.get("Period", "").strip(), header.strip(), val, None))
        if rec["period"]:
            core.append(rec)
    return core, kpis


def write_db(ticker, core, kpis):
    import duckdb
    db = REPO / ticker / "Reports" / f"{ticker}.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    con.execute(schema.create_sql())
    con.execute("DELETE FROM core_metrics")
    con.execute("DELETE FROM kpis")
    if core:
        cols = ", ".join(schema.CORE_NAMES)
        ph = ", ".join("?" * len(schema.CORE_NAMES))
        con.executemany(
            f"INSERT INTO core_metrics ({cols}) VALUES ({ph})",
            [[r[c] for c in schema.CORE_NAMES] for r in core],
        )
    if kpis:
        con.executemany("INSERT INTO kpis VALUES (?, ?, ?, ?)", kpis)
    con.close()
    return db


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    report_only = "--report" in sys.argv

    if args:
        paths = []
        for t in args:
            p = REPO / t / "Reports" / f"{t}_Metrics.csv"
            if p.exists():
                paths.append(p)
            else:
                print(f"  !! no metrics CSV for {t}", file=sys.stderr)
    else:
        paths = sorted(REPO.glob("*/Reports/*_Metrics.csv"))

    unmapped_freq = collections.Counter()
    ok = failed = 0
    coverage = []

    for p in paths:
        ticker = p.parent.parent.name
        try:
            rows, headers, unmapped = read_csv(p)
            core, kpis = to_core(rows)
        except Exception as e:
            print(f"  ERROR {ticker}: {e}", file=sys.stderr)
            failed += 1
            continue

        for h in unmapped:
            unmapped_freq[h] += 1

        mapped = len(headers) - len(unmapped)
        coverage.append((ticker, len(core), mapped, len(headers), unmapped))

        if not report_only and core:
            write_db(ticker, core, kpis)
        ok += 1

    print(f"{'ticker':12s} {'rows':>5s} {'mapped':>12s}  unmapped -> kpis")
    print("-" * 74)
    for ticker, nrows, mapped, total, unmapped in coverage:
        extra = ", ".join(unmapped[:3]) + ("..." if len(unmapped) > 3 else "")
        print(f"{ticker:12s} {nrows:5d} {mapped:5d}/{total:<6d}  {extra}")

    print("-" * 74)
    print(f"  {ok} ticker(s) loaded, {failed} failed")
    if unmapped_freq:
        print(f"\n  most common unmapped headers (these become KPI rows):")
        for h, c in unmapped_freq.most_common(15):
            print(f"    {c:3d}x  {h}")
    if report_only:
        print("\n  (--report: no databases written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
