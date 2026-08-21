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
import pathlib
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import duckdb
import periods
import schema
import sections
from parsers import common

REPO = pathlib.Path(__file__).resolve().parents[1]

SHORTLIST = 4
SIZE_BUDGET = 40_000          # bytes; ARB.NZ's agent ingested 489 KB of filings
CONTEXT_CHARS = (100, 40, 0)  # progressive trim to stay inside the budget

SCALES: dict[str, float] = {"units": 1.0, "thousands": 1e3, "millions": 1e6,
                            "billions": 1e9}
TIER = {"statement_line": 3, "prior_year_column": 2, "prose": 1}
# Where on the page a candidate sits. A summary table's first column is
# often the OLDEST year; notes restate pieces of a total.
SECTION_SCORE = {"statement": 2, "other": 1, "notes": 0, "summary": -1}
# Metrics for which a printed 0 is a dash row, not a value.
NONZERO = {"revenue", "total_assets", "total_liabilities", "shareholders_equity",
           "shares_outstanding", "net_income"}
# Metrics that cannot be negative; a negative match is a stray line.
NONNEG = {"revenue", "total_assets", "total_liabilities", "shares_outstanding",
          "cash_and_equivalents"}
# Metrics that legitimately repeat the same figure across periods.
STATIC_EXEMPT = {"shares_outstanding", "eps", "dividend_per_share"}
STATIC_PERIODS = 3
# A lone candidate this far from the metric's other periods is a stray match.
OUTLIER_RATIO = 20.0
OUTLIER_MIN_PERIODS = 3
# Metrics whose right line depends on a definition the agent owns: capex
# with or without intangibles, total vs attributable profit, basic vs
# diluted EPS, which borrowings lines make "total debt". A candidate here
# can be the right line and the wrong answer, so it is proposed, never green.
JUDGMENT = {"capex", "total_debt", "ebitda", "stock_based_comp", "eps", "net_income"}
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
    section: str = "other"   # from sections.py; "other" when unindexed

    def rank(self) -> tuple[int, int, int, int, str]:
        # Context keywords stand in for the section only when the file was
        # not indexed; inside a known section they would double-count.
        ctx = self.context if self.section == "other" else ""
        score = (1 if STATEMENT_RE.search(ctx) else 0) - (1 if NOTE_RE.search(ctx) else 0)
        # Primary statements precede the notes, so an earlier line wins ties.
        return (-TIER.get(self.confidence, 0), -SECTION_SCORE.get(self.section, 1),
                -score, self.line_no, self.source_file)

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
    flag: str | None = None    # "confirm-definition" for JUDGMENT metrics
    section: str | None = None  # section of the resolving candidate


def _canon(label: str | None) -> str | None:
    if not label:
        return None
    p = periods.parse(label)
    return periods.canonical(p) if p.fiscal_year is not None else label


def _later(a: str | None, b: str) -> bool:
    return a is not None and periods.sort_key(a) > periods.sort_key(b)


def _year_like(c: Candidate) -> bool:
    """A column header read as a value: `EBITDA  2024  2023`."""
    v = c.value_raw
    return v == int(v) and 1990 <= v <= 2040


def _guard(metric: str, c: Candidate, static: set[float]) -> str | None:
    """Why a statement-line candidate may not resolve a cell on its own."""
    if _year_like(c):
        return "year-like values (column headers read as numbers)"
    if c.section == "summary":
        return "summary-table values (often another year's column)"
    if c.value_raw in static:
        return f"values that repeat in {STATIC_PERIODS}+ periods (static text, not a figure)"
    if c.value_raw == 0 and metric in NONZERO:
        return "zero/dash rows"
    if c.value_raw < 0 and metric in NONNEG:
        return "negative values for a metric that cannot be negative"
    return None


def _resolve(metric: str, kind: str, period: str, cands: list[Candidate],
             static: set[float]) -> Proposal:
    guarded: Counter[str] = Counter()
    stmt: list[Candidate] = []
    for c in cands:
        if c.confidence != "statement_line":
            continue
        why_not = _guard(metric, c, static)
        if why_not:
            guarded[why_not] += 1
        else:
            stmt.append(c)
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
    ranked = sorted(cands, key=Candidate.rank)
    if pick is not None:
        flag = "confirm-definition" if metric in JUDGMENT else None
        # Keep the distinct statement values visible for definition calls.
        distinct: list[Candidate] = []
        for c in ranked:
            if flag and c.confidence == "statement_line" \
                    and c.value_raw not in {d.value_raw for d in distinct}:
                distinct.append(c)
        return Proposal(metric, kind, period, "resolved", rung, pick.value_raw,
                        pick.units_hint, pick.currency, pick.source_file,
                        pick.line_no, len(cands), why, distinct[:SHORTLIST], [], flag,
                        pick.section)
    if stmt:
        why = f"{len(values)} distinct statement values"
    elif guarded:
        why = "only " + "; ".join(k for k, _ in guarded.most_common())
    else:
        why = "no statement-line candidate; weaker evidence only"
    return Proposal(metric, kind, period, "contested", None, None, None, None,
                    None, None, len(cands), why, ranked[:SHORTLIST])


def propose(con: duckdb.DuckDBPyConnection,
            secs: dict[str, list[sections.Section]] | None = None) -> list[Proposal]:
    """Run the ladder over every (metric, period) the facts table touches.

    `secs` (from sections.index_ticker) tags each candidate with the section
    it sits in; without it every candidate is "other".
    """
    secs = secs or {}
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
        section = sections.section_of(secs[src], int(line or 0)) if src in secs else "other"
        cells[key][p].append(Candidate(float(value), units, ccy, src, int(line or 0),
                                       ctx or "", conf, own[src], section))

    universe = {p for p in own.values() if p}
    universe |= {p for by in cells.values() for p, cs in by.items()
                 if any(c.confidence == "statement_line" for c in cs)}
    out: list[Proposal] = []
    for (metric, kind), by_period in cells.items():
        # A figure printed identically in several periods' own statements is
        # almost never a period figure (ARG.NZ capex "33,220" x5).
        seen_in: dict[float, set[str]] = defaultdict(set)
        for period, cs in by_period.items():
            for c in cs:
                if c.confidence == "statement_line":
                    seen_in[c.value_raw].add(period)
        static = ({v for v, ps in seen_in.items() if len(ps) >= STATIC_PERIODS}
                  if metric not in STATIC_EXEMPT else set())
        cells_out = []
        for period in universe | set(by_period):
            cands = by_period.get(period, [])
            if cands:
                cells_out.append(_resolve(metric, kind, period, cands, static))
            else:
                files = sorted(f for f, p in own.items() if p == period)
                cells_out.append(Proposal(metric, kind, period, "missing", files=files,
                                          rationale="no candidate in facts"))
        out.extend(_demote_outliers(cells_out))

    order = {n: i for i, n in enumerate(schema.CORE_NAMES)}
    out.sort(key=lambda p: (p.kind != "core", order.get(p.metric, 999), p.metric,
                            periods.sort_key(p.period)))
    return out


def _demote_outliers(cells: list[Proposal]) -> list[Proposal]:
    """A `single` cell 20x away from the metric's typical magnitude becomes
    contested. Needs enough resolved history to know what typical is; signs
    are ignored so a loss year is not an outlier."""
    resolved = [p for p in cells if p.status == "resolved" and p.value_raw]
    if len(resolved) < OUTLIER_MIN_PERIODS + 1:
        return cells
    for p in cells:
        if p.rung != "single" or not p.value_raw:
            continue
        others = sorted(abs(q.value_raw or 0) for q in resolved if q is not p and q.value_raw)
        if len(others) < OUTLIER_MIN_PERIODS:
            continue
        typical = others[len(others) // 2]
        ratio = abs(p.value_raw) / typical if typical else 0
        if ratio > OUTLIER_RATIO or ratio < 1 / OUTLIER_RATIO:
            p.status, p.rung = "contested", None
            p.rationale = (f"single candidate {_fmt(p.value_raw)} is out of line with "
                           f"the metric's other periods (typical {_fmt(typical)})")
            p.shortlist = [Candidate(p.value_raw, p.units_hint, p.currency,
                                     p.source_file or "", p.line_no or 0, "",
                                     "statement_line", None, p.section or "other")]
            p.value_raw = p.units_hint = p.currency = p.source_file = None
            p.line_no = None
            p.flag = None
    return cells


# ---------------------------------------------------------------------------
# Worksheet
# ---------------------------------------------------------------------------

SYMBOL = {"resolved": "✓", "contested": "?", "missing": "✗"}


def _symbol(p: Proposal) -> str:
    return "~" if p.flag else SYMBOL[p.status]


def _fmt(v: float) -> str:
    return f"{v:g}"


ABBR = {"statement_line": "stmt", "prior_year_column": "prior", "prose": "prose"}


def _short(file: str, ticker: str) -> str:
    """Drop the ticker prefix and .txt: `ARB.NZ_Annual_FY2024.txt` -> `Annual_FY2024`."""
    name = file.removesuffix(".txt")
    return name.removeprefix(f"{ticker}_")


def _render(ticker: str, props: list[Proposal],
            pointers: dict[str, list[tuple[str, int, int]]], ctx_width: int,
            cap: int) -> str:
    core = [p for p in props if p.kind == "core"]
    kpi = [p for p in props if p.kind == "kpi"]
    plist = sorted({p.period for p in props}, key=periods.sort_key)
    metrics = [m for m in schema.CORE_NAMES if any(p.metric == m for p in core)]
    grid = {(p.metric, p.period): _symbol(p) for p in core}
    n = Counter(p.status for p in core)

    flagged = sum(1 for p in core if p.flag)
    intro = (f"{len(core)} core cells: {n['resolved'] - flagged} resolved (✓), "
             f"{flagged} proposed pending a definition call (~), "
             f"{n['contested']} contested (?), {n['missing']} missing (✗). "
             "Values are exactly as printed (see Filings for each file's "
             "scale); nothing here is rescaled or written to core_metrics. "
             f"File names omit the `{ticker}_` prefix and `.txt`.")
    lines: list[str] = [f"# {ticker} adjudication worksheet", "", intro,
                        "", "## Grid", "",
                        "| period | " + " | ".join(metrics) + " |",
                        "|---|" + "---|" * len(metrics)]
    lines.extend(f"| {per} | " + " | ".join(grid.get((m, per), "·") for m in metrics)
                 + " |" for per in plist)

    def resolved_line(per: str, got: list[Proposal]) -> str:
        # One line per period. The dominant source file and its units are
        # named once; a cell from another file says so inline.
        files = Counter((p.source_file or "", p.units_hint or "?") for p in got)
        (dom_file, dom_units), _ = files.most_common(1)[0]
        parts = []
        for p in got:
            cell = f"{p.metric}={_fmt(p.value_raw or 0)}@{p.line_no}"
            if (p.source_file or "", p.units_hint or "?") != (dom_file, dom_units):
                cell += f" ({_short(p.source_file or '', ticker)}, {p.units_hint or '?'})"
            parts.append(cell)
        return f"- {per} · {_short(dom_file, ticker)} · {dom_units}: " + " · ".join(parts)

    def shortlist_line(p: Proposal) -> str:
        items = []
        for c in p.shortlist[:cap]:
            ctx = c.first_line(ctx_width)
            items.append(f"{_fmt(c.value_raw)} [{ABBR.get(c.confidence, c.confidence)}"
                         f"/{c.section}{'' if c.units_hint else ', ?units'}] "
                         f"{_short(c.source_file, ticker)}:{c.line_no}"
                         + (f" — {ctx}" if ctx else ""))
        return f"- {p.metric} {p.period} ({p.n_candidates}; {p.rationale}): " + " | ".join(items)

    lines += ["", "## Resolved", ""]
    for per in plist:
        got = [p for p in core if p.period == per and p.status == "resolved" and not p.flag]
        if got:
            lines.append(resolved_line(per, got))

    lines += ["", "## Confirm definition", "",
              ("The candidates settle on a line, but which line is the metric is a "
               "definition call (capex with/without intangibles, total vs attributable "
               "profit, basic vs diluted EPS, which borrowings are debt). First value "
               "is the proposal; the rest are the other distinct statement values."), ""]
    lines.extend(shortlist_line(p) for p in core if p.flag)

    lines += ["", "## Contested", "",
              ("Ranked candidates; first is the scanner's best guess. Tags are "
               "[evidence/section]: stmt = statement line, prior = a later filing's "
               "comparative column; section is where on the page it sits."), ""]
    lines.extend(shortlist_line(p) for p in core if p.status == "contested")

    lines += ["", "## Missing", ""]
    for per in plist:
        miss = [p for p in core if p.period == per and p.status == "missing"]
        if not miss:
            continue
        files = sorted({f for p in miss for f in p.files})
        lines.append(f"- {per}: {', '.join(p.metric for p in miss)}"
                     + (f" — files: {', '.join(_short(f, ticker) for f in files)}"
                        if files else " — no filing for this period"))
        for f in files:
            for caption, a, b in pointers.get(f, []):
                lines.append(f"  - {f}:{a}-{b} {caption}")

    lines += ["", "## KPIs", "",
              "Metrics outside core_metrics (write to `kpis`, not new columns):", ""]
    for per in plist:
        got = [p for p in kpi if p.period == per and p.status == "resolved"]
        if got:
            lines.append(resolved_line(per, got))
    lines.append("")
    lines.extend(shortlist_line(p) for p in kpi if p.status == "contested")

    lines += ["", "## Filings", "",
              "| file | own period | units hint | currency | candidates |",
              "|---|---|---|---|---|"]
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
        lines.append(f"| {f} | {own_p or '?'} | {u}{flag} | "
                     f"{ccy.most_common(1)[0][0]} | {cnt} |")
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


PER_SHARE = {"eps", "dividend_per_share"}
# Outflows the filings print in parentheses and agents store either way.
SIGN_FREE = {"capex", "cost_of_revenue", "total_liabilities", "total_debt"}


def _close(got: float, actual: float) -> bool:
    # core_metrics is written to one decimal: allow that rounding as well
    # as a 0.5% relative band for large figures.
    return abs(got - actual) <= max(0.051, 0.005 * abs(actual))


def _agrees(metric: str, proposed: float, hint: str | None, actual: float,
            units: str | None) -> bool:
    if metric in PER_SHARE:
        ratios = [1.0, 0.01, 100.0]          # cents vs dollars
    elif hint in SCALES and units in SCALES:
        ratios = [SCALES[hint] / SCALES[units]]
    else:
        ratios = [10.0 ** e for e in range(-9, 10, 3)]
    for r in ratios:
        got = proposed * r
        if _close(got, actual) or (metric in SIGN_FREE and _close(-got, actual)):
            return True
    return False


def _why(metric: str, p: Proposal, actual: dict[str, dict[str, Any]]) -> str:
    """Name the likely cause of a disagreement so the residual is legible."""
    have = actual[p.period]
    if p.value_raw is None:
        return "other"
    for other, rec in actual.items():
        if other != p.period and rec.get(metric) is not None and _agrees(
                metric, p.value_raw, p.units_hint, float(rec[metric]), rec.get("units")):
            return "period-shift"
    if _agrees(metric, p.value_raw, p.units_hint, float(have[metric]), None):
        return "scale"
    return "other"


def check(con: duckdb.DuckDBPyConnection, props: list[Proposal]) -> dict[str, Any]:
    """Grade resolved core cells against an existing core_metrics."""
    present = {r[0] for r in con.execute("DESCRIBE core_metrics").fetchall()}
    cols = [c for c in schema.CORE_NAMES if c in present]
    actual: dict[str, dict[str, Any]] = {}
    for row in con.execute(f"SELECT {', '.join(cols)} FROM core_metrics").fetchall():
        rec = dict(zip(cols, row, strict=True))
        key = _canon(rec["period"])
        if key:
            actual[key] = rec
    by: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    bad: dict[tuple[str, str], tuple[float, float]] = {}
    why: dict[tuple[str, str], str] = {}
    resolved = compared = agree = 0
    firm = [0, 0, 0]   # agree, compared, value-ok over cells shown as ✓ (no flag)
    by_sec: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for p in props:
        if p.kind != "core" or p.status != "resolved":
            continue
        resolved += 1
        have = actual.get(p.period)
        if not have or have.get(p.metric) is None or p.value_raw is None:
            continue
        compared += 1
        by[p.rung or ""][1] += 1
        firm[1] += not p.flag
        sec_key = (p.rung or "", p.section or "other")
        by_sec[sec_key][1] += not p.flag
        if _agrees(p.metric, p.value_raw, p.units_hint, float(have[p.metric]),
                   have.get("units")):
            agree += 1
            by[p.rung or ""][0] += 1
            firm[0] += not p.flag
            by_sec[sec_key][0] += not p.flag
        else:
            bad[(p.metric, p.period)] = (p.value_raw, float(have[p.metric]))
            cause = _why(p.metric, p, actual)
            why[(p.metric, p.period)] = cause
            value_ok = cause in ("scale", "period-shift")
            firm[2] += (not p.flag) and value_ok
            by_sec[sec_key][0] += (not p.flag) and value_ok
    return {"resolved": resolved, "compared": compared, "agree": agree,
            "by_rung": {k: (v[0], v[1]) for k, v in by.items()},
            "disagreements": bad, "why": why, "firm": (firm[0], firm[1]),
            "firm_value": (firm[0] + firm[2], firm[1]),
            "by_section": {k: (v[0], v[1]) for k, v in by_sec.items()}}


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
        secs = sections.index_ticker(t, REPO)
        props = propose(con, secs)
        write_table(con, props)
        text = worksheet(t, props, {f: sections.pointers(ss) for f, ss in secs.items()})
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
            print("  ✓ cells value-level by (rung, section): "
                  + ", ".join(f"{r}/{sct} {a}/{m}" for (r, sct), (a, m)
                              in sorted(rep["by_section"].items())))
            causes = Counter(rep["why"].values())
            fa, fc = rep["firm"]
            fv, _ = rep["firm_value"]
            if fc:
                print(f"  ✓ cells only (definition-sensitive metrics excluded): "
                      f"{fa}/{fc} ({100.0 * fa / fc:.1f}%) strict, "
                      f"{fv}/{fc} ({100.0 * fv / fc:.1f}%) value-level")
            value_ok = rep["agree"] + causes["scale"] + causes["period-shift"]
            if rep["compared"]:
                print(f"  value-level: {value_ok}/{rep['compared']} "
                      f"({100.0 * value_ok / rep['compared']:.1f}%) -- the right "
                      "number; scale and period label are the agent's call")
            if causes:
                print("    disagreements by likely cause: "
                      + ", ".join(f"{k} {v}" for k, v in causes.most_common()))
            for (m, per), (got, want) in sorted(rep["disagreements"].items()):
                print(f"    ✗ {m} {per}: proposed {got:g}, core has {want:g}"
                      f"  [{rep['why'][(m, per)]}]")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
