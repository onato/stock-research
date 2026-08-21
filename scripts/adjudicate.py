#!/usr/bin/env python3
"""Pre-adjudicate `facts` into a worksheet the financial-parser agent reviews.

`build_facts.py` emits every candidate value and deliberately never picks a
winner. The agent then adjudicated by re-reading the filings: on ARB.NZ, 81
of its 124 tool calls were grep/Read over Extracted/*.txt and only ~5 touched
`facts` -- 65% of the most expensive stage in the pipeline spent repeating a
search that had already been done.

This script sits in the empty seam between `facts` and `core_metrics`. It is
deterministic and decides only what the candidates themselves settle:

  resolved/single        one statement-line candidate
  resolved/unanimous     several candidates, one value
  resolved/corroborated  several values, but a LATER filing's comparative
                         column repeats exactly one of them
  contested              otherwise -- a ranked shortlist for the agent
  missing                no candidate -- names the filing to look in

It never scales units (`units_hint` is the scale printed on the page, carried
through untouched), never writes `core_metrics`, and never reads a filing.
The judgment rules (SBC double-counting, buyback authorizations, units
sanity) stay with the agent; this just stops it paying for search.

Outputs: `proposed_metrics` table in the ticker DB (rebuilt each run) and
`Reports/{TICKER}_Worksheet.md`, sized to be read in one call.

Usage:
  adjudicate.py TICKER            # write table + worksheet, print a summary
  adjudicate.py TICKER --check    # also grade resolved cells against the
                                  # core_metrics an agent already wrote
"""

import argparse
import importlib
import pathlib
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import duckdb
import periods
import schema
from parsers import common

REPO = pathlib.Path(__file__).resolve().parents[1]

SHORTLIST = 4
SIZE_BUDGET = 40_000          # bytes; ARB.NZ's agent ingested 489 KB of filings
CONTEXT_CHARS = (100, 40, 0)  # progressive trim to stay inside the budget

SCALES: dict[str, float] = {"units": 1.0, "thousands": 1e3, "millions": 1e6,
                            "billions": 1e9}
TIER = {"statement_line": 3, "prior_year_column": 2, "prose": 1}
STATEMENT_RE = re.compile(
    r"consolidated|statement of|balance sheet|income statement|cash flow", re.IGNORECASE)
NOTE_RE = re.compile(r"\bnotes?\b|segment|reconcil", re.IGNORECASE)


@dataclass
class Candidate:
    value_raw: float
    units_hint: str | None
    currency: str | None
    source_file: str
    line_no: int
    context: str
    confidence: str
    own_period: str | None   # the filing's own period, canonically spelled

    def rank(self) -> tuple[int, int, int, str]:
        ctx = self.context or ""
        score = (1 if STATEMENT_RE.search(ctx) else 0) - (1 if NOTE_RE.search(ctx) else 0)
        # Primary statements precede the notes, so an earlier line wins ties.
        return (-TIER.get(self.confidence, 0), -score, self.line_no, self.source_file)

    def first_line(self, width: int) -> str:
        if width <= 0:
            return ""
        line = next((ln.strip() for ln in (self.context or "").splitlines()
                     if ln.strip()), "")
        return line[:width]


@dataclass
class Proposal:
    metric: str
    kind: str                  # "core" | "kpi"
    period: str
    status: str                # "resolved" | "contested" | "missing"
    rung: str | None = None
    value_raw: float | None = None
    units_hint: str | None = None
    currency: str | None = None
    source_file: str | None = None
    line_no: int | None = None
    n_candidates: int = 0
    rationale: str = ""
    shortlist: list[Candidate] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def _canon(label: str | None) -> str | None:
    if not label:
        return None
    p = periods.parse(label)
    return periods.canonical(p) if p.fiscal_year is not None else label


def _later(a: str | None, b: str) -> bool:
    return a is not None and periods.sort_key(a) > periods.sort_key(b)


def _resolve(metric: str, kind: str, period: str,
             cands: list[Candidate]) -> Proposal:
    stmt = [c for c in cands if c.confidence == "statement_line"]
    values = {c.value_raw for c in stmt}
    pick: Candidate | None = None
    rung = ""
    why = ""
    if len(stmt) == 1:
        pick, rung = stmt[0], "single"
    elif len(values) == 1 and stmt:
        pick, rung = min(stmt, key=Candidate.rank), "unanimous"
        why = f"{len(stmt)} candidates agree"
    elif stmt:
        comps = [c for c in cands if c.confidence == "prior_year_column"
                 and _later(c.own_period, period)]
        matched = {c.value_raw for c in comps if c.value_raw in values}
        if len(matched) == 1:
            (v,) = matched
            pick = min((c for c in stmt if c.value_raw == v), key=Candidate.rank)
            rung = "corroborated"
            src = sorted({c.source_file for c in comps if c.value_raw == v})
            why = f"repeated by the comparative column of {', '.join(src)}"
    if pick is not None:
        return Proposal(metric, kind, period, "resolved", rung, pick.value_raw,
                        pick.units_hint, pick.currency, pick.source_file,
                        pick.line_no, len(cands), why)
    ranked = sorted(cands, key=Candidate.rank)
    return Proposal(metric, kind, period, "contested", None, None, None, None,
                    None, None, len(cands),
                    f"{len(values)} distinct statement values" if stmt
                    else "no statement-line candidate; weaker evidence only",
                    ranked[:SHORTLIST])


def propose(con: duckdb.DuckDBPyConnection) -> list[Proposal]:
    """Run the ladder over every (metric, period) the facts table touches."""
    rows = con.execute(
        "SELECT metric, period, value_raw, units_hint, source_file, line_no,"
        " context, confidence, currency FROM facts").fetchall()
    own: dict[str, str | None] = {}
    cells: dict[tuple[str, str], dict[str, list[Candidate]]] = defaultdict(
        lambda: defaultdict(list))
    for metric, period, value, units, src, line, ctx, conf, ccy in rows:
        if src not in own:
            own[src] = _canon(common.period_from_filename(src))
        p = _canon(period)
        if p is None or value is None:
            continue
        core = schema.normalize(metric)
        key = (core, "core") if core else (metric, "kpi")
        cells[key][p].append(Candidate(float(value), units, ccy, src, int(line or 0),
                                       ctx or "", conf, own[src]))

    universe = {p for p in own.values() if p}
    universe |= {p for by in cells.values() for p, cs in by.items()
                 if any(c.confidence == "statement_line" for c in cs)}
    out: list[Proposal] = []
    for (metric, kind), by_period in cells.items():
        for period in universe | set(by_period):
            cands = by_period.get(period, [])
            if cands:
                out.append(_resolve(metric, kind, period, cands))
            else:
                files = sorted(f for f, p in own.items() if p == period)
                out.append(Proposal(metric, kind, period, "missing", files=files,
                                    rationale="no candidate in facts"))

    order = {n: i for i, n in enumerate(schema.CORE_NAMES)}
    out.sort(key=lambda p: (p.kind != "core", order.get(p.metric, 999), p.metric,
                            periods.sort_key(p.period)))
    return out


# ---------------------------------------------------------------------------
# Worksheet
# ---------------------------------------------------------------------------

SYMBOL = {"resolved": "✓", "contested": "?", "missing": "✗"}


def _fmt(v: float) -> str:
    return f"{v:g}"


def _render(ticker: str, props: list[Proposal],
            pointers: dict[str, list[tuple[str, int, int]]], ctx_width: int,
            cap: int) -> str:
    core = [p for p in props if p.kind == "core"]
    kpi = [p for p in props if p.kind == "kpi"]
    plist = sorted({p.period for p in props}, key=periods.sort_key)
    metrics = [m for m in schema.CORE_NAMES if any(p.metric == m for p in core)]
    grid = {(p.metric, p.period): SYMBOL[p.status] for p in core}
    n = Counter(p.status for p in core)

    intro = (f"{len(core)} core cells: {n['resolved']} resolved, "
             f"{n['contested']} contested, {n['missing']} missing. "
             "Values are exactly as printed (see Filings for each file's "
             "scale); nothing here is rescaled or written to core_metrics.")
    lines: list[str] = [f"# {ticker} adjudication worksheet", "", intro,
                        "", "## Grid", "",
                        "| period | " + " | ".join(metrics) + " |",
                        "|---|" + "---|" * len(metrics)]
    lines.extend(f"| {per} | " + " | ".join(grid.get((m, per), "·") for m in metrics)
                 + " |" for per in plist)

    lines += ["", "## Resolved", ""]
    for per in plist:
        got = [p for p in core if p.period == per and p.status == "resolved"]
        if got:
            lines.append(f"- {per}: " + " · ".join(
                f"{p.metric}={_fmt(p.value_raw or 0)} [{p.units_hint or '?'}, "
                f"{p.source_file}:{p.line_no}]" for p in got))

    def shortlist(p: Proposal) -> None:
        lines.append(f"### {p.metric} {p.period} ({p.n_candidates} candidates; {p.rationale})")
        for c in p.shortlist[:cap]:
            ctx = c.first_line(ctx_width)
            lines.append(f"- {_fmt(c.value_raw)} [{c.confidence}, {c.units_hint or '?'}] "
                     f"{c.source_file}:{c.line_no}" + (f" — {ctx}" if ctx else ""))
        lines.append("")

    lines += ["", "## Contested", ""]
    for p in core:
        if p.status == "contested":
            shortlist(p)

    lines += ["## Missing", ""]
    for per in plist:
        miss = [p for p in core if p.period == per and p.status == "missing"]
        if not miss:
            continue
        files = sorted({f for p in miss for f in p.files})
        lines.append(f"- {per}: {', '.join(p.metric for p in miss)}"
                 + (f" — files: {', '.join(files)}" if files else " — no filing for this period"))
        for f in files:
            for caption, a, b in pointers.get(f, []):
                lines.append(f"  - {f}:{a}-{b} {caption}")

    lines += ["", "## KPIs", "",
          "Metrics outside core_metrics (write to `kpis`, not new columns):", ""]
    for per in plist:
        got = [p for p in kpi if p.period == per and p.status == "resolved"]
        if got:
            lines.append(f"- {per}: " + " · ".join(
                f"{p.metric}={_fmt(p.value_raw or 0)} [{p.units_hint or '?'}, "
                f"{p.source_file}:{p.line_no}]" for p in got))
    lines.append("")
    for p in kpi:
        if p.status == "contested":
            shortlist(p)

    lines += ["## Filings", "", "| file | own period | units hint | currency | candidates |",
          "|---|---|---|---|---|"]
    per_file: dict[str, list[Candidate]] = defaultdict(list)
    for p in props:
        for c in p.shortlist:
            per_file[c.source_file].append(c)
    seen: dict[str, tuple[str | None, Counter[str], Counter[str], int]] = {}
    for p in props:
        cs = p.shortlist or []
        if p.status == "resolved" and p.source_file:
            cs = [*cs, Candidate(p.value_raw or 0, p.units_hint, p.currency,
                                 p.source_file, p.line_no or 0, "", "", None)]
        for c in cs:
            own_p, units, ccy, cnt = seen.get(c.source_file, (None, Counter(), Counter(), 0))
            units[c.units_hint or "NULL"] += 1
            ccy[c.currency or "NULL"] += 1
            seen[c.source_file] = (c.own_period or own_p, units, ccy, cnt + 1)
    for f in sorted(seen):
        own_p, units, ccy, cnt = seen[f]
        u = units.most_common(1)[0][0]
        flag = " ⚠ no units on page" if u == "NULL" else ""
        lines.append(f"| {f} | {own_p or '?'} | {u}{flag} | {ccy.most_common(1)[0][0]} | {cnt} |")
    lines.append("")
    return "\n".join(lines)


def worksheet(ticker: str, props: list[Proposal],
              pointers: dict[str, list[tuple[str, int, int]]]) -> str:
    """Render the worksheet, trimming context and shortlists to the budget."""
    text = ""
    for cap in (SHORTLIST, 2):
        for width in CONTEXT_CHARS:
            text = _render(ticker, props, pointers, width, cap)
            if len(text.encode()) <= SIZE_BUDGET:
                return text
    return text


# ---------------------------------------------------------------------------
# DB table + check
# ---------------------------------------------------------------------------

def write_table(con: duckdb.DuckDBPyConnection, props: list[Proposal]) -> None:
    con.execute("DROP TABLE IF EXISTS proposed_metrics")
    con.execute("""CREATE TABLE proposed_metrics (
        metric TEXT, kind TEXT, period TEXT, status TEXT, rung TEXT,
        value_raw DOUBLE, units_hint TEXT, currency TEXT, source_file TEXT,
        line_no INTEGER, n_candidates INTEGER, rationale TEXT)""")
    con.executemany(
        "INSERT INTO proposed_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(p.metric, p.kind, p.period, p.status, p.rung, p.value_raw, p.units_hint,
          p.currency, p.source_file, p.line_no, p.n_candidates, p.rationale)
         for p in props])


def _agrees(proposed: float, hint: str | None, actual: float,
            units: str | None) -> bool:
    if hint in SCALES and units in SCALES:
        ratios = [SCALES[hint] / SCALES[units]]
    else:
        ratios = [10.0 ** e for e in range(-9, 10, 3)]
    for r in ratios:
        got = proposed * r
        if abs(got - actual) <= 0.005 * max(abs(actual), 1e-9):
            return True
    return False


def check(con: duckdb.DuckDBPyConnection, props: list[Proposal]) -> dict[str, Any]:
    """Grade resolved core cells against an existing core_metrics."""
    cols = schema.CORE_NAMES
    actual: dict[str, dict[str, Any]] = {}
    for row in con.execute(f"SELECT {', '.join(cols)} FROM core_metrics").fetchall():
        rec = dict(zip(cols, row, strict=True))
        key = _canon(rec["period"])
        if key:
            actual[key] = rec
    by: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    bad: dict[tuple[str, str], tuple[float, float]] = {}
    resolved = compared = agree = 0
    for p in props:
        if p.kind != "core" or p.status != "resolved":
            continue
        resolved += 1
        have = actual.get(p.period)
        if not have or have.get(p.metric) is None or p.value_raw is None:
            continue
        compared += 1
        by[p.rung or ""][1] += 1
        if _agrees(p.value_raw, p.units_hint, float(have[p.metric]), have.get("units")):
            agree += 1
            by[p.rung or ""][0] += 1
        else:
            bad[(p.metric, p.period)] = (p.value_raw, float(have[p.metric]))
    return {"resolved": resolved, "compared": compared, "agree": agree,
            "by_rung": {k: (v[0], v[1]) for k, v in by.items()},
            "disagreements": bad}


def section_pointers(ticker: str) -> dict[str, list[tuple[str, int, int]]]:
    """Statement line ranges per extracted filing, for the Missing section.

    Supplied by scripts/sections.py when present; an empty map degrades the
    worksheet to naming the file only.
    """
    try:
        sections = importlib.import_module("sections")
    except ImportError:
        return {}
    result: dict[str, list[tuple[str, int, int]]] = sections.index_ticker(ticker, REPO)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--check", action="store_true",
                    help="grade resolved cells against existing core_metrics")
    args = ap.parse_args()
    t = args.ticker
    reports = REPO / "research" / t / "Reports"
    db = reports / f"{t}.duckdb"
    if not db.exists():
        print(f"{t}: no facts -- {db} does not exist; run extract.py first",
              file=sys.stderr)
        return 2
    con = duckdb.connect(str(db))
    try:
        try:
            n = con.execute("SELECT count(*) FROM facts").fetchone()
        except duckdb.Error:
            n = (0,)
        if not n or not n[0]:
            print(f"{t}: no facts rows to adjudicate", file=sys.stderr)
            return 2
        props = propose(con)
        write_table(con, props)
        text = worksheet(t, props, section_pointers(t))
        out = reports / f"{t}_Worksheet.md"
        out.write_text(text)
        core = [p for p in props if p.kind == "core"]
        c = Counter(p.status for p in core)
        print(f"{t}: {len(core)} core cells -- {c['resolved']} resolved, "
              f"{c['contested']} contested, {c['missing']} missing; "
              f"{sum(1 for p in props if p.kind == 'kpi')} KPI cells")
        print(f"  Worksheet: {out} ({len(text.encode()) / 1024:.1f} KB)")
        if args.check:
            rep = check(con, props)
            pct = 100.0 * rep["agree"] / rep["compared"] if rep["compared"] else 0.0
            print(f"  check: {rep['agree']}/{rep['compared']} resolved cells agree "
                  f"with core_metrics ({pct:.1f}%)")
            for rung, (a, m) in sorted(rep["by_rung"].items()):
                print(f"    {rung:13s} {a:4d}/{m:<4d}")
            for (m, per), (got, want) in sorted(rep["disagreements"].items()):
                print(f"    ✗ {m} {per}: proposed {got:g}, core has {want:g}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
