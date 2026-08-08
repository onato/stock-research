#!/usr/bin/env python3
"""Data integrity across the research corpus: what we have, what is missing.

The goal this serves is ten years of complete financial history per company.
Nothing else in the repo measures progress toward it -- `make status` counts
folders and `index.html` ranks valuation upside, neither of which says whether
a company's numbers are actually there.

Three decisions shape what gets measured:

*Source of truth is the CSV, not the DuckDB.* CLAUDE.md makes the committed
`{TICKER}_Metrics.csv` the system of record and the `.duckdb` files gitignored
rebuildable caches. Several of those caches drifted badly from their own CSV
(BABA's reads 4.8% filled against a 96.5%-filled CSV; FRFHF's holds no FY rows
at all against a complete one). Scoring the DBs would invent gaps that do not
exist, so this reads the CSVs -- and reports cache drift separately, because a
stale cache is itself a thing worth fixing.

*Complete means the eight fields a DCF needs*, not every column in the schema.
Company-specific KPI columns (FacebookMAU, VehicleSales, OccupancyRate) run
~10% filled by nature; counting them would mark every interesting company
incomplete and bury the real gaps.

*Header spellings drift.* EPSDiluted, FCF and Equity are the same metrics as
EPS, FreeCashFlow and ShareholdersEquity, so completeness resolves through
schema.normalize() rather than exact-matching headers -- otherwise the report
sends someone re-parsing a file that was already correct.

Usage:
  integrity_report.py [--root DIR] [--json OUT.json] [--html OUT.html] [--top N]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import pathlib
import re
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import schema

REPO = pathlib.Path(__file__).resolve().parents[1]

# The fields a DCF cannot be built without. A ticker-year counts as complete
# only when every one of them is present; see the module docstring for why
# this is narrower than schema.CORE_COLUMNS.
CORE8: list[str] = [
    "revenue", "net_income", "eps", "operating_cash_flow",
    "capex", "free_cash_flow", "shareholders_equity", "shares_outstanding",
]

# Non-numeric bookkeeping columns, excluded from any fill calculation.
NON_METRIC = {"period", "units", "currency"}

# The goal, in years of complete history.
TARGET_YEARS = 10

# CSV header spelling for each core column, for display.
DISPLAY = {name: hdr for name, _, hdr in schema.CORE_COLUMNS}

YEAR_RE = re.compile(r"(19|20)\d{2}")


# Scale/currency markers some tickers bake into their headers:
# BABA and 9988.HK file `Revenue_RMB_Mn` and `EPS_Diluted_RMB`, AGL.NZ uses
# `EPS_cents` and `SharesOutstanding_m`, AAPL `SharesDilutedM`. These are the
# core metrics wearing a unit suffix, not company KPIs, and reading them as
# KPIs reported BABA at "100% filled, 0 complete years".
#
# Only trailing tokens are stripped, and only ones that are purely a unit or
# currency. A leading qualifier still means a different metric -- AWSRevenue
# and iPhoneRevenue are segment lines, not the company's revenue, and must
# keep failing to resolve.
UNIT_TOKENS = {
    "mn", "m", "bn", "b", "k", "000s", "thousands", "millions", "billions",
    "cents", "cent", "pct", "percent",
    "usd", "nzd", "aud", "hkd", "gbp", "eur", "rmb", "cny", "jpy", "sgd",
    "us", "nz",
}


def core_field(header: str) -> str | None:
    """Map a CSV header to its core column, or None if it is a company KPI."""
    name = schema.normalize(header)
    if name is None:
        name = _strip_units(header)
    if name is None or name in NON_METRIC:
        return None
    return name


def _strip_units(header: str) -> str | None:
    """Retry normalize() with trailing unit/currency tokens removed."""
    parts = re.split(r"[_\s]+", str(header).strip())
    while len(parts) > 1 and parts[-1].lower() in UNIT_TOKENS:
        parts.pop()
        name = schema.normalize("_".join(parts))
        if name is not None:
            return name
    # Suffix glued on without a separator, e.g. `SharesDilutedM`.
    single = "".join(parts)
    m = re.match(r"^(.*?)(Mn|M|Bn|B|K)$", single)
    if m and len(m.group(1)) > 3:
        return schema.normalize(m.group(1))
    return None


def year_of(period: str) -> int | None:
    m = YEAR_RE.search(period or "")
    return int(m.group(0)) if m else None


def is_fy(period: str) -> bool:
    """True for a full-year row. H1/Q rows are real data but not extra years."""
    return (period or "").strip().upper().startswith("FY")


def read_csv_rows(path: pathlib.Path) -> list[dict[str, str]]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


def score_csv(path: pathlib.Path) -> dict[str, Any]:
    """Year depth and per-field fill for one ticker's metrics CSV.

    Only FY rows count toward depth: half-year and quarterly rows are real
    data but do not add years of history, and counting them would overstate
    how close the corpus is to the ten-year goal.
    """
    rows = read_csv_rows(path)
    empty: dict[str, Any] = {
        "fy_years": 0, "complete_years": 0, "cell_fill_pct": 0.0,
        "first_year": None, "latest_year": None,
        "per_field_fill": {}, "missing_fields": [],
    }
    if not rows:
        return empty

    # Resolve headers once; a CSV may repeat a core metric under two
    # spellings, so keep every header that maps to each core column.
    headers: dict[str, list[str]] = {}
    for h in rows[0]:
        name = core_field(h) if h else None
        if name:
            headers.setdefault(name, []).append(h)
    if not headers:
        return empty

    def present(row: dict[str, str], name: str) -> bool:
        return any((row.get(h) or "").strip() for h in headers.get(name, []))

    # Deduplicate by year: a CSV occasionally repeats a period, and a year
    # counted twice would inflate depth against the goal.
    by_year: dict[int, dict[str, str]] = {}
    for row in rows:
        period = row.get("Period") or ""
        if not is_fy(period):
            continue
        y = year_of(period)
        if y is None:
            continue
        by_year.setdefault(y, row)

    if not by_year:
        return empty

    filled = total = 0
    complete = 0
    field_hits: dict[str, int] = dict.fromkeys(headers, 0)
    for row in by_year.values():
        for name in headers:
            total += 1
            if present(row, name):
                filled += 1
                field_hits[name] += 1
        if all(present(row, n) for n in CORE8):
            complete += 1

    n_years = len(by_year)
    per_field = {
        DISPLAY.get(n, n): round(100 * hits / n_years, 1)
        for n, hits in field_hits.items()
    }
    missing = sorted(
        DISPLAY.get(n, n) for n in CORE8
        if n not in headers or field_hits.get(n, 0) < n_years
    )
    return {
        "fy_years": n_years,
        "complete_years": complete,
        "cell_fill_pct": round(100 * filled / total, 1) if total else 0.0,
        "first_year": min(by_year),
        "latest_year": max(by_year),
        "per_field_fill": per_field,
        "missing_fields": missing,
    }


def db_status(db_path: pathlib.Path, csv_years: int, csv_complete: int) -> str:
    """Compare the local DuckDB cache against the CSV that governs it.

    The caches are gitignored and rebuildable, and several were left
    half-adjudicated -- BABA's held FY rows whose core columns were almost
    entirely NULL against a complete CSV. That is a rebuild signal, not
    missing data, so it is reported on its own axis instead of being folded
    into the coverage score.

    Row count alone would miss the BABA case, so the cache must match the CSV
    on *complete* years too. A broken cache degrades to a flag rather than
    taking down the whole report.
    """
    if not db_path.exists():
        return "DB_MISSING"
    cols = ", ".join(CORE8)
    try:
        import duckdb
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = con.execute(
                f"SELECT period, {cols} FROM core_metrics").fetchall()
        finally:
            con.close()
    except Exception:
        return "DB_UNREADABLE"

    db_years: set[int | None] = set()
    complete = 0
    for r in rows:
        if not r or not is_fy(r[0]):
            continue
        db_years.add(year_of(r[0]))
        if all(v is not None for v in r[1:]):
            complete += 1
    db_years.discard(None)
    if len(db_years) < csv_years or complete < csv_complete:
        return "DB_STALE"
    return "DB_OK"


def exchange_of(ticker: str) -> str:
    """Suffix after the dot; bare tickers are US listings."""
    return ticker.rsplit(".", 1)[1] if "." in ticker else "US"


def stage_of(rec: dict[str, Any]) -> str:
    """Which step of the pipeline this ticker has reached."""
    if rec["complete_years"] >= TARGET_YEARS:
        return "complete-10yr"
    if not rec["has_csv"]:
        return "filings-only" if rec["has_filings"] else "empty"
    return "valued" if rec["has_dcf"] else "parsed"


def scan(root: pathlib.Path | str) -> list[dict[str, Any]]:
    """One integrity record per ticker folder under {root}/research."""
    root = pathlib.Path(root)
    research = root / "research"
    if not research.is_dir():
        return []

    records: list[dict[str, Any]] = []
    for d in sorted(p for p in research.iterdir() if p.is_dir()):
        ticker = d.name
        reports = d / "Reports"
        csv_path = reports / f"{ticker}_Metrics.csv"
        extracted = sorted((d / "Extracted").glob("*")) if (d / "Extracted").is_dir() else []

        rec: dict[str, Any] = {
            "ticker": ticker,
            "exchange": exchange_of(ticker),
            "n_extracted": len(extracted),
            "has_filings": bool(extracted),
            "has_csv": csv_path.is_file(),
            "has_duckdb": (reports / f"{ticker}.duckdb").is_file(),
            "has_dcf": (reports / f"{ticker}_DCF.json").is_file(),
            "has_analysis": (reports / f"{ticker}_Analysis.json").is_file(),
            "has_dashboard": (reports / f"{ticker}_Dashboard.html").is_file(),
        }
        rec.update(score_csv(csv_path) if rec["has_csv"] else {
            "fy_years": 0, "complete_years": 0, "cell_fill_pct": 0.0,
            "first_year": None, "latest_year": None,
            "per_field_fill": {}, "missing_fields": [],
        })
        rec["db_status"] = (
            db_status(reports / f"{ticker}.duckdb", rec["fy_years"],
                      rec["complete_years"])
            if rec["has_csv"] else "DB_MISSING")
        rec["stage"] = stage_of(rec)
        records.append(rec)
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Corpus-level rollups: the funnel, the backlog, the weakest fields."""
    parsed = [r for r in records if r["has_csv"]]

    by_exchange: dict[str, dict[str, int]] = {}
    for r in records:
        e = by_exchange.setdefault(r["exchange"], {
            "exchange": r["exchange"], "total": 0, "parsed": 0,
            "filings_only": 0, "empty": 0, "complete_10yr": 0})
        e["total"] += 1
        if r["has_csv"]:
            e["parsed"] += 1
            if r["complete_years"] >= TARGET_YEARS:
                e["complete_10yr"] += 1
        elif r["has_filings"]:
            e["filings_only"] += 1
        else:
            e["empty"] += 1

    # Per-field fill weighted by ticker-year, so a company with 20 years of
    # history counts more than one with three.
    hits: dict[str, float] = {}
    years: dict[str, int] = {}
    for r in parsed:
        n = r["fy_years"]
        for field, pct in r["per_field_fill"].items():
            hits[field] = hits.get(field, 0.0) + pct * n / 100
            years[field] = years.get(field, 0) + n
    field_fill = sorted(
        ({"field": f, "pct": round(100 * hits[f] / years[f], 1),
          "years": years[f]} for f in hits if years[f]),
        key=lambda x: (x["pct"], x["field"]))

    depth: dict[int, int] = {}
    for r in parsed:
        depth[r["fy_years"]] = depth.get(r["fy_years"], 0) + 1

    db_counts: dict[str, int] = {}
    for r in parsed:
        db_counts[r["db_status"]] = db_counts.get(r["db_status"], 0) + 1

    n = len(parsed)
    return {
        "tracked": len(records),
        "with_filings": sum(1 for r in records if r["has_filings"]),
        "parsed": n,
        "valued": sum(1 for r in parsed if r["has_dcf"]),
        "complete_10yr": sum(1 for r in parsed
                             if r["complete_years"] >= TARGET_YEARS),
        "mean_fill_pct": round(sum(r["cell_fill_pct"] for r in parsed) / n, 1)
        if n else 0.0,
        "mean_years": round(sum(r["fy_years"] for r in parsed) / n, 1)
        if n else 0.0,
        "total_extracted": sum(r["n_extracted"] for r in records),
        "by_exchange": sorted(by_exchange.values(),
                              key=lambda e: -e["total"]),
        "field_fill": field_fill,
        "year_depth": sorted(depth.items()),
        "db_counts": db_counts,
        "target_years": TARGET_YEARS,
    }


def load_companies(root: pathlib.Path) -> dict[str, Any]:
    try:
        with open(root / "state" / "companies.json") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# --------------------------------------------------------------------------
# Rendering. Charts are inline SVG rather than a charting library: every
# dashboard in this repo opens straight from file:// with no server and no
# network, and a CDN <script> would break that.
# --------------------------------------------------------------------------

def esc(x: object) -> str:
    return html.escape(str(x))

STAGE_COLORS = {
    "complete-10yr": "#00d4aa",
    "valued": "#4a9eff",
    "parsed": "#ffb400",
    "filings-only": "#ff6b6b",
    "empty": "#5a5a72",
}

STAGE_LABELS = {
    "complete-10yr": "10yr complete",
    "valued": "Valued (DCF)",
    "parsed": "Parsed",
    "filings-only": "Filings only",
    "empty": "No filings",
}

# summarize() counts per exchange under SQL-ish keys; the stacked chart draws
# them with the stage palette. Mapping one to the other here keeps the two
# vocabularies from having to match.
EXCHANGE_SEGMENTS: list[tuple[str, str]] = [
    ("complete_10yr", "complete-10yr"),
    ("parsed", "parsed"),
    ("filings_only", "filings-only"),
    ("empty", "empty"),
]


def bar_chart(items: list[tuple[str, float, str]], *, width: int = 720,
              row_h: int = 26, label_w: int = 132, suffix: str = "") -> str:
    """Horizontal bars: (label, value, color). Values scale to the max."""
    if not items:
        return "<p class='muted'>No data.</p>"
    top = max(v for _, v, _ in items) or 1
    bar_w = width - label_w - 66
    rows = []
    for i, (label, value, color) in enumerate(items):
        y = i * row_h
        w = max(1.0, bar_w * value / top)
        rows.append(
            f'<text x="0" y="{y + 15}" class="lbl">{esc(label)}</text>'
            f'<rect x="{label_w}" y="{y + 4}" width="{w:.1f}" height="14" '
            f'rx="3" fill="{color}"/>'
            f'<text x="{label_w + w + 7:.1f}" y="{y + 15}" class="val">'
            f'{value:g}{suffix}</text>')
    h = len(items) * row_h
    return (f'<svg viewBox="0 0 {width} {h}" width="100%" height="{h}" '
            f'role="img">{"".join(rows)}</svg>')


def stacked_chart(rows: list[dict[str, Any]], segments: list[tuple[str, str]],
                  *, width: int = 720, row_h: int = 30,
                  label_w: int = 92) -> str:
    """One stacked bar per exchange; segments are (count_key, stage) pairs.

    `parsed` in the per-exchange counts already includes the 10yr-complete
    tickers, so the complete segment is drawn first and netted out of parsed
    -- stacking both raw would show more tickers than the exchange has.
    """
    if not rows:
        return "<p class='muted'>No data.</p>"

    def segs(r: dict[str, Any]) -> list[tuple[str, int]]:
        out = []
        for key, stage in segments:
            v = r.get(key, 0)
            if key == "parsed":
                v -= r.get("complete_10yr", 0)
            out.append((stage, max(v, 0)))
        return out

    top = max(sum(v for _, v in segs(r)) for r in rows) or 1
    bar_w = width - label_w - 74
    out = []
    for i, r in enumerate(rows):
        y = i * row_h
        x = float(label_w)
        total = 0
        for stage, v in segs(r):
            total += v
            if not v:
                continue
            w = bar_w * v / top
            out.append(
                f'<rect x="{x:.1f}" y="{y + 5}" width="{w:.1f}" height="16" '
                f'fill="{STAGE_COLORS[stage]}"><title>{esc(r["exchange"])} '
                f'{esc(STAGE_LABELS[stage])}: {v}</title></rect>')
            x += w
        out.append(
            f'<text x="0" y="{y + 18}" class="lbl">{esc(r["exchange"])}</text>'
            f'<text x="{x + 7:.1f}" y="{y + 18}" class="val">{total}</text>')
    h = len(rows) * row_h
    return (f'<svg viewBox="0 0 {width} {h}" width="100%" height="{h}" '
            f'role="img">{"".join(out)}</svg>')


def depth_chart(depth: list[tuple[int, int]], target: int) -> str:
    """Histogram of FY-year depth, with the target marked.

    Everything past `target + 5` collapses into one overflow bucket: a couple
    of very deep tickers (NFLX has 20 years) otherwise stretch the axis until
    the 3-to-13-year range -- where every actionable ticker actually sits --
    is squeezed into a corner.
    """
    if not depth:
        return "<p class='muted'>No parsed tickers yet.</p>"
    cap = target + 5
    binned: dict[int, int] = {}
    for years, count in depth:
        binned[min(years, cap)] = binned.get(min(years, cap), 0) + count

    width, height, pad = 720, 190, 26
    top = max(binned.values()) or 1
    slot = (width - pad * 2) / (cap + 1)
    bars = []
    for years, count in sorted(binned.items()):
        x = pad + years * slot
        h = (height - 46) * count / top
        color = (STAGE_COLORS["complete-10yr"] if years >= target
                 else STAGE_COLORS["parsed"])
        label = f"{cap}+" if years == cap else str(years)
        bars.append(
            f'<rect x="{x:.1f}" y="{height - 30 - h:.1f}" '
            f'width="{max(slot - 3, 2):.1f}" height="{h:.1f}" rx="2" '
            f'fill="{color}"><title>{count} ticker(s) with {label} FY '
            f'year(s)</title></rect>'
            f'<text x="{x + slot / 2:.1f}" y="{height - 16}" '
            f'class="tick" text-anchor="middle">{label}</text>')
    tx = pad + target * slot - 2
    bars.append(
        f'<line x1="{tx:.1f}" y1="6" x2="{tx:.1f}" y2="{height - 30}" '
        f'stroke="#00d4aa" stroke-width="1.5" stroke-dasharray="4 3"/>'
        f'<text x="{tx + 6:.1f}" y="16" class="val">{target}yr goal</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" '
            f'height="{height}" role="img">{"".join(bars)}</svg>')


def render(records: list[dict[str, Any]], summary: dict[str, Any],
           companies: dict[str, Any]) -> str:
    """The full self-contained dashboard."""
    s = summary
    tracked = s["tracked"] or 1
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    tiles = [
        ("Tickers tracked", s["tracked"], ""),
        ("With filings", s["with_filings"],
         f'{100 * s["with_filings"] / tracked:.0f}% of tracked'),
        ("Parsed dataset", s["parsed"],
         f'{100 * s["parsed"] / tracked:.0f}% of tracked'),
        (f'{s["target_years"]}yr complete', s["complete_10yr"],
         f'{100 * s["complete_10yr"] / tracked:.0f}% of tracked'),
        ("Mean cell fill", f'{s["mean_fill_pct"]}%', "of parsed tickers"),
        ("Mean FY years", s["mean_years"], "of parsed tickers"),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="tval">{esc(v)}</div>'
        f'<div class="tlab">{esc(label)}</div>'
        f'<div class="tsub">{esc(sub)}</div></div>'
        for label, v, sub in tiles)

    funnel = bar_chart([
        ("Tracked", s["tracked"], "#5a5a72"),
        ("Filings on disk", s["with_filings"], STAGE_COLORS["filings-only"]),
        ("Parsed dataset", s["parsed"], STAGE_COLORS["parsed"]),
        ("Valued (DCF)", s["valued"], STAGE_COLORS["valued"]),
        (f'{s["target_years"]}yr complete', s["complete_10yr"],
         STAGE_COLORS["complete-10yr"]),
    ])

    backlog = stacked_chart(s["by_exchange"], EXCHANGE_SEGMENTS)
    legend = "".join(
        f'<span class="key"><i style="background:{STAGE_COLORS[k]}"></i>'
        f'{esc(STAGE_LABELS[k])}</span>'
        for k in ["complete-10yr", "parsed", "filings-only", "empty"])

    fields = bar_chart(
        [(f["field"], f["pct"],
          STAGE_COLORS["filings-only"] if f["pct"] < 50
          else STAGE_COLORS["parsed"] if f["pct"] < 85
          else STAGE_COLORS["complete-10yr"])
         for f in s["field_fill"]], suffix="%")

    depth = depth_chart(s["year_depth"], s["target_years"])

    # Table: parsed tickers first (most actionable), worst coverage first.
    order = {"complete-10yr": 0, "valued": 1, "parsed": 2,
             "filings-only": 3, "empty": 4}
    ranked = sorted(
        records,
        key=lambda r: (order.get(r["stage"], 9), -r["complete_years"],
                       -r["cell_fill_pct"], r["ticker"]))
    body = []
    for r in ranked:
        co = companies.get(r["ticker"]) or {}
        name = co.get("name") or ""
        gap = max(0, s["target_years"] - r["complete_years"]) if r["has_csv"] else s["target_years"]
        miss = ", ".join(r["missing_fields"][:4]) or "—"
        link = (f'<a href="research/{esc(r["ticker"])}/Reports/'
                f'{esc(r["ticker"])}_Dashboard.html">{esc(r["ticker"])}</a>'
                if r["has_dashboard"] else esc(r["ticker"]))
        fill = r["cell_fill_pct"]
        fill_cls = "pos" if fill >= 85 else "warn-text" if fill >= 50 else "neg"
        db = r["db_status"]
        db_cls = {"DB_OK": "ok", "DB_STALE": "badge warn",
                  "DB_UNREADABLE": "badge bad", "DB_MISSING": "muted"}[db]
        body.append(
            "<tr>"
            f'<td>{link}</td>'
            f'<td class="co">{esc(name)}</td>'
            f'<td>{esc(r["exchange"])}</td>'
            f'<td><span class="badge" style="background:{STAGE_COLORS[r["stage"]]}22;'
            f'color:{STAGE_COLORS[r["stage"]]}">{esc(STAGE_LABELS[r["stage"]])}</span></td>'
            f'<td>{r["fy_years"]}</td>'
            f'<td>{r["complete_years"]}</td>'
            f'<td>{gap or "—"}</td>'
            f'<td class="{fill_cls}">{fill:g}%</td>'
            f'<td>{esc(r["latest_year"] or "—")}</td>'
            f'<td>{r["n_extracted"]}</td>'
            f'<td><span class="{db_cls}">{esc(db.replace("DB_", "").lower())}</span></td>'
            f'<td class="co">{esc(miss)}</td>'
            "</tr>")

    stale = [r for r in records if r["db_status"] in ("DB_STALE", "DB_UNREADABLE")]
    if stale:
        items = "".join(
            f'<li><b>{esc(r["ticker"])}</b> — CSV has {r["fy_years"]} FY '
            f'year(s), cache is {esc(r["db_status"].replace("DB_", "").lower())}'
            f'</li>' for r in sorted(stale, key=lambda r: -r["fy_years"]))
        drift = (
            f'<p class="muted">{len(stale)} local DuckDB cache(s) are behind '
            f'their committed CSV. The CSV is the system of record, so the data '
            f'is not lost — but cross-ticker SQL reads the cache. Rebuild with '
            f'<code>make facts TICKER=X</code>.</p><ul class="drift">{items}</ul>')
    else:
        drift = '<p class="muted">Every local cache matches its CSV.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Data Integrity</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    color: #e0e0e0; min-height: 100vh; max-width: 1180px;
    margin: 0 auto; padding: 20px;
}}
.header, .card {{
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px; padding: 20px 25px; margin-bottom: 20px;
}}
h1 {{
    font-size: 1.6em;
    background: linear-gradient(90deg, #00d4aa, #00b894);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
}}
h2 {{ font-size: 1.05em; color: #00d4aa; margin-bottom: 14px; }}
.meta {{ color: #8b8ba0; margin-top: 6px; font-size: 0.9em; }}
.muted {{ color: #8b8ba0; font-size: 0.86em; line-height: 1.6; }}
a {{ color: #00d4aa; text-decoration: none; }}
a:hover {{ color: #00b894; }}
code {{ background: rgba(255,255,255,0.08); padding: 1px 5px; border-radius: 4px; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; }}
.tile {{
    background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px; padding: 14px 16px;
}}
.tval {{ font-size: 1.9em; font-weight: 600; color: #00d4aa; }}
.tlab {{ font-size: 0.9em; margin-top: 2px; }}
.tsub {{ color: #8b8ba0; font-size: 0.76em; margin-top: 2px; }}
/* align-items:start stops a short card being stretched to the height of a
   tall neighbour, which left a block of dead space under the funnel. */
.grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: start; }}
@media (max-width: 900px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
svg .lbl {{ fill: #c9c9d8; font-size: 12px; }}
svg .val {{ fill: #8b8ba0; font-size: 11px; }}
svg .tick {{ fill: #8b8ba0; font-size: 10px; }}
.key {{ font-size: 0.78em; color: #8b8ba0; margin-right: 14px; white-space: nowrap; }}
.key i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.88em; }}
th {{
    color: #00d4aa; text-align: left; cursor: pointer; user-select: none;
    padding: 8px 9px; border-bottom: 1px solid rgba(255,255,255,0.15); white-space: nowrap;
}}
th.asc::after {{ content: " \\25B2"; font-size: 0.8em; }}
th.desc::after {{ content: " \\25BC"; font-size: 0.8em; }}
td {{ padding: 6px 9px; border-bottom: 1px solid rgba(255,255,255,0.06); white-space: nowrap; }}
td.co {{ white-space: normal; max-width: 240px; line-height: 1.3; color: #b9b9cc; }}
tr:hover td {{ background: rgba(255,255,255,0.04); }}
.pos {{ color: #00d4aa; }} .neg {{ color: #ff6b6b; }} .warn-text {{ color: #ffb400; }}
.ok {{ color: #8b8ba0; font-size: 0.85em; }}
.badge {{ display: inline-block; font-size: 0.72em; padding: 2px 7px; border-radius: 8px; }}
.badge.warn {{ background: rgba(255,180,0,0.15); color: #ffb400; }}
.badge.bad {{ background: rgba(255,107,107,0.15); color: #ff6b6b; }}
.controls {{ display: flex; align-items: center; gap: 15px; margin-bottom: 16px; }}
.search-box {{
    flex: 1; padding: 11px 15px; font-size: 1em; color: #e0e0e0;
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px; outline: none;
}}
.search-box:focus {{ border-color: #00d4aa; }}
.count {{ color: #8b8ba0; white-space: nowrap; font-size: 0.9em; }}
.drift {{ margin: 10px 0 0 18px; font-size: 0.86em; line-height: 1.7; color: #c9c9d8; }}
.scroll {{ overflow-x: auto; }}
</style>
</head>
<body>
<div class="header">
<h1>Data Integrity</h1>
<p class="meta">{s["tracked"]} tickers &middot; {s["total_extracted"]:,} filings extracted
&middot; goal: {s["target_years"]} years of complete history per company
&middot; as of {now}</p>
</div>

<div class="card"><div class="tiles">{tile_html}</div></div>

<div class="grid2">
  <div class="card">
    <h2>Pipeline</h2>
    {funnel}
    <p class="muted">Filings are downloaded far faster than they are parsed.
    The drop from filings to parsed is the backlog.</p>
  </div>
  <div class="card">
    <h2>By exchange</h2>
    {backlog}
    <div style="margin-top:10px">{legend}</div>
  </div>
</div>

<div class="grid2">
  <div class="card">
    <h2>Years of history per parsed ticker</h2>
    {depth}
    <p class="muted">Bars at or past the dashed line clear the
    {s["target_years"]}-year goal.</p>
  </div>
  <div class="card">
    <h2>Field fill rate (worst first)</h2>
    {fields}
    <p class="muted">Share of parsed ticker-years where the field is present.
    The bottom of this list is the extraction to-do list.</p>
  </div>
</div>

<div class="card">
  <h2>DuckDB cache drift</h2>
  {drift}
</div>

<div class="card">
  <h2>Every ticker</h2>
  <div class="controls">
    <input type="text" class="search-box" id="search"
      placeholder="Search by ticker, company, exchange or stage&hellip;">
    <span class="count" id="count"></span>
  </div>
  <div class="scroll">
  <table id="tbl">
  <thead><tr>
    <th data-type="str">Ticker</th><th data-type="str">Company</th>
    <th data-type="str">Exch</th><th data-type="str">Stage</th>
    <th data-type="num">FY yrs</th><th data-type="num">Complete</th>
    <th data-type="num">Gap</th><th data-type="num">Fill</th>
    <th data-type="num">Latest</th><th data-type="num">Files</th>
    <th data-type="str">Cache</th><th data-type="str">Missing fields</th>
  </tr></thead>
  <tbody>{"".join(body)}</tbody>
  </table>
  </div>
</div>

<p class="muted">Measured from each ticker's committed
<code>{{TICKER}}_Metrics.csv</code>, the system of record. A ticker-year counts
as complete when all eight DCF-critical fields are present: Revenue, NetIncome,
EPS, OperatingCashFlow, CapEx, FreeCashFlow, ShareholdersEquity,
SharesOutstanding. Company-specific KPI columns are excluded. Regenerate with
<code>make integrity</code>.</p>

<script>
(function () {{
  var table = document.getElementById('tbl');
  var tbody = table.tBodies[0];
  var rows = Array.prototype.slice.call(tbody.rows);
  var search = document.getElementById('search');
  var count = document.getElementById('count');

  function shown() {{
    return rows.filter(function (r) {{ return r.style.display !== 'none'; }}).length;
  }}
  function updateCount() {{
    count.textContent = shown() + ' of ' + rows.length + ' tickers';
  }}
  search.addEventListener('input', function () {{
    var q = search.value.toLowerCase();
    rows.forEach(function (r) {{
      r.style.display = r.textContent.toLowerCase().indexOf(q) === -1 ? 'none' : '';
    }});
    updateCount();
  }});

  Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th, i) {{
    th.addEventListener('click', function () {{
      var numeric = th.dataset.type === 'num';
      var asc = !th.classList.contains('asc');
      Array.prototype.forEach.call(table.tHead.rows[0].cells, function (o) {{
        o.classList.remove('asc', 'desc');
      }});
      th.classList.add(asc ? 'asc' : 'desc');
      rows.sort(function (a, b) {{
        var x = a.cells[i].textContent.trim();
        var y = b.cells[i].textContent.trim();
        if (numeric) {{
          var nx = parseFloat(x.replace(/[^0-9.\\-]/g, ''));
          var ny = parseFloat(y.replace(/[^0-9.\\-]/g, ''));
          if (isNaN(nx)) nx = -Infinity;
          if (isNaN(ny)) ny = -Infinity;
          return asc ? nx - ny : ny - nx;
        }}
        return asc ? x.localeCompare(y) : y.localeCompare(x);
      }});
      rows.forEach(function (r) {{ tbody.appendChild(r); }});
    }});
  }});
  updateCount();
}})();
</script>
</body>
</html>
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(REPO), help="repo root to scan")
    p.add_argument("--json", dest="json_out", default=None,
                   help="write the full report here")
    p.add_argument("--html", dest="html_out", default=None,
                   help="write the dashboard here")
    p.add_argument("--top", type=int, default=15,
                   help="rows of weakest-coverage detail to print")
    args = p.parse_args()

    root = pathlib.Path(args.root)
    if not (root / "research").is_dir():
        print(f"integrity_report: no research/ directory under {root}",
              file=sys.stderr)
        return 1

    records = scan(root)
    summary = summarize(records)

    if args.json_out:
        out = pathlib.Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump({"generated": dt.datetime.now().isoformat(timespec="seconds"),
                       "summary": summary, "tickers": records}, f, indent=1)
    if args.html_out:
        out = pathlib.Path(args.html_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            f.write(render(records, summary, load_companies(root)))

    s = summary
    print(f"{s['tracked']} tickers  "
          f"{s['with_filings']} with filings  "
          f"{s['parsed']} parsed  "
          f"{s['complete_10yr']} at {s['target_years']}yr complete")
    print(f"mean cell fill {s['mean_fill_pct']}%  "
          f"mean {s['mean_years']} FY years across parsed tickers")

    weak = [r for r in records if r["has_csv"]]
    weak.sort(key=lambda r: (r["complete_years"], r["cell_fill_pct"]))
    if weak and args.top:
        print("\nweakest parsed tickers (complete years / fill):")
        for r in weak[:args.top]:
            print(f"  {r['ticker']:<12} {r['complete_years']:>3}yr complete"
                  f"  {r['cell_fill_pct']:>5}% fill"
                  f"  missing: {', '.join(r['missing_fields'][:4]) or '—'}")
    if args.html_out:
        print(f"\nwrote {args.html_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
