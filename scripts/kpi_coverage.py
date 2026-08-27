#!/usr/bin/env python3
"""Which stored KPIs reach a dashboard, and which are stranded.

`kpis` is long-form and uncanonicalised, so it accumulates names nothing
consumes. Only `schema.PROMOTE_KPIS` entries become CSV columns, and only a
CSV column can be charted -- so an unrecognised spelling is stored forever
and never seen. This is the read-only report that says which is which:

  promoted  reaches the Metrics CSV, chartable today
  blocked   an owner-FCF component; the DCF reads it via dcf_context.py
  unmapped  stored but stranded -- a candidate for the vocabulary

Costs nothing to run: no model, no network.

Usage: kpi_coverage.py [TICKER]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import schema

REPO = pathlib.Path(__file__).resolve().parents[1]


def classify(names: list[str]) -> dict[str, list[str]]:
    """Split canonical KPI names into promoted / blocked / unmapped."""
    out: dict[str, set[str]] = {"promoted": set(), "blocked": set(), "unmapped": set()}
    for name in names:
        canon = schema.normalize_kpi(name)
        if schema.promote_header(name):
            out["promoted"].add(canon)
        elif canon in schema.DCF_COMPONENT_KPIS:
            out["blocked"].add(canon)
        else:
            out["unmapped"].add(canon)
    return {k: sorted(v) for k, v in out.items()}


def survey(repo: pathlib.Path, ticker: str | None = None
           ) -> dict[str, dict[str, list[str]]]:
    """{ticker: {promoted, blocked, unmapped}} over every ticker DB."""
    import duckdb

    pattern = f"{ticker}/Reports/{ticker}.duckdb" if ticker else "*/Reports/*.duckdb"
    rows: dict[str, dict[str, list[str]]] = {}
    for db in sorted((repo / "research").glob(pattern)):
        try:
            con = duckdb.connect(str(db), read_only=True)
            names = [r[0] for r in con.execute("SELECT DISTINCT name FROM kpis").fetchall()]
            con.close()
        except duckdb.Error:
            continue
        rows[db.stem] = classify(names)
    return rows


def main(argv: list[str]) -> int:
    ticker = argv[1] if len(argv) > 1 else None
    rows = survey(REPO, ticker)
    if not rows:
        print("no ticker DBs found", file=sys.stderr)
        return 1
    unmapped_total: dict[str, int] = {}
    for name, r in sorted(rows.items()):
        if not any(r.values()):
            continue
        print(f"{name}")
        for kind in ("promoted", "blocked", "unmapped"):
            if r[kind]:
                print(f"  {kind:9} {', '.join(r[kind])}")
        for n in r["unmapped"]:
            unmapped_total[n] = unmapped_total.get(n, 0) + 1
    if unmapped_total and ticker is None:
        print("\nUnmapped names by ticker count (candidates for PROMOTE_KPIS):")
        ranked = sorted(unmapped_total.items(), key=lambda kv: (-kv[1], kv[0]))
        for n, count in ranked[:25]:
            print(f"  {count:3}  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
