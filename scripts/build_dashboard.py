#!/usr/bin/env python3
"""Render `{T}_Dashboard.html` deterministically from a small Spec JSON.

    python3 scripts/build_dashboard.py TPW.AX [--out path]

Inputs, all under research/{T}/Reports/:
  {T}_DashboardSpec.json  -- written by the dashboard-generator agent: the
                             descriptor line, KPI cards, chart sections and
                             the per-metric help text. A few KB.
  {T}_Metrics.csv         -- embedded verbatim as the `csvData` literal.
  {T}_Analysis.json       -- embedded as `analysis`.
  {T}_DCF.json            -- embedded as `dcfData` (optional; the valuation
                             section and header chip are omitted without it).

Everything else -- CSS, chart wiring, the overview/guidance renderers, the
interactive DCF section -- lives in scripts/templates/dashboard.html. The
agent used to re-emit all of that per ticker (54k output tokens on TPW.AX)
and the verify gate exists because it kept getting the escaping and the
DCF field names wrong. Here the JSON is embedded with json.dumps, the series
are computed in Python from the CSV, and the DCF cards are anchored to the
JSON's own numbers at every scenario's defaults.

Spec contract (validated by `validate_spec`):

  descriptor            one-line business descriptor (HTML entities allowed)
  currency, units       display prefix/suffix for money KPIs ("A$", "m")
  kpis[]                {label, format: money|pct|number|text,
                         column | dcf_path | value,
                         change: "yoy" | literal text, positive?, note?}
  sections[]            {title, subtitle?, charts[]}
  charts[]              {id, title, help, series[], type?, log?, percent?,
                         interim? (default true), annotation?, y_title?,
                         y1_title?, fill?}
  series[]              {label, column | derive | dcf_path, kind?: bar|line,
                         axis?: y|y1, color?}
                        derive: "yoy:<col>" | "ratio:<num>/<den>" (percent)
  metric_descriptions   {help_key: {title, content(html)}}
  dcf?                  {base_fcf_label?, growth_hint?, current_price_note?,
                         base_sublabel?}
"""

from __future__ import annotations

import csv
import html
import io
import json
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

import dcf_fields as F
import periods

REPO = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = pathlib.Path(__file__).resolve().parent / "templates" / "dashboard.html"

FORMATS = ("money", "pct", "number", "text")
KINDS = ("bar", "line")


class SpecError(ValueError):
    """The Spec references something that does not exist or is malformed."""


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def read_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """Parse the Metrics CSV and sort rows chronologically via periods."""
    reader = csv.DictReader(io.StringIO(text.strip()))
    cols = list(reader.fieldnames or [])
    if not cols or cols[0] != "Period":
        raise SpecError("Metrics CSV must start with a Period column")
    rows = [dict(r) for r in reader if r.get("Period")]
    rows.sort(key=lambda r: periods.sort_key(r["Period"]))
    return cols, rows


def annual_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if periods.is_annual(periods.parse(r["Period"]))]


def interim_kind(rows: list[dict[str, str]]) -> str | None:
    """'half' when H1/H2 rows exist, 'quarterly' for Q rows, else None.

    Quarters win when both appear: the finer grain is the one a reader
    switching off Annual wants.
    """
    types = {periods.parse(r["Period"]).ptype for r in rows}
    if any(t.startswith("Q") for t in types):
        return "quarterly"
    if types & {"H1", "H2"}:
        return "half"
    return None


def interim_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    kind = interim_kind(rows)
    if kind is None:
        return []
    want = ("Q1", "Q2", "Q3", "Q4") if kind == "quarterly" else ("H1", "H2")
    return [r for r in rows if periods.parse(r["Period"]).ptype in want]


def short_label(label: str) -> str:
    """FY2026 -> FY26; H1 FY2026 / H1-2026 -> H1'26; Q3 2025 -> Q3'25."""
    p = periods.parse(label)
    if p.fiscal_year is None or p.ptype == "OTHER":
        return label
    yy = f"{p.fiscal_year % 100:02d}"
    if p.ptype == "FY":
        return f"FY{yy}"
    return f"{p.ptype}'{yy}"


def num(v: str | None) -> float | None:
    if v is None:
        return None
    s = v.strip()
    if not s or s.lower() in ("nan", "null", "none", "n/a"):
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def series(rows: list[dict[str, str]], column: str) -> list[float | None]:
    return [num(r.get(column)) for r in rows]


# ---------------------------------------------------------------------------
# Derived series
# ---------------------------------------------------------------------------

def _prior_index(rows: list[dict[str, str]], i: int) -> int | None:
    """Index of the same period one fiscal year earlier, if present."""
    p = periods.parse(rows[i]["Period"])
    want = periods.prior_year(p)
    if want is None:
        return None
    for j in range(i - 1, -1, -1):
        if periods.canonical(periods.parse(rows[j]["Period"])) == want:
            return j
    return None


def derive(rows: list[dict[str, str]], expr: str) -> list[float | None]:
    op, _, arg = expr.partition(":")
    if op == "yoy":
        vals = series(rows, arg)
        out: list[float | None] = []
        for i, v in enumerate(vals):
            j = _prior_index(rows, i)
            prev = vals[j] if j is not None else None
            out.append(None if v is None or not prev else (v - prev) / abs(prev) * 100)
        return out
    if op == "ratio":
        top, _, bottom = arg.partition("/")
        if not top or not bottom:
            raise SpecError(f"ratio derive needs num/den: {expr!r}")
        a, b = series(rows, top), series(rows, bottom)
        return [None if x is None or not y else x / y * 100 for x, y in zip(a, b, strict=True)]
    raise SpecError(f"unknown derive {expr!r} (use yoy:<col> or ratio:<num>/<den>)")


def dcf_get(dcf: dict[str, Any] | None, path: str) -> Any:
    cur: Any = dcf
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def dcf_series(rows: list[dict[str, str]], dcf: dict[str, Any] | None,
               path: str) -> list[float | None]:
    """A series keyed by period label inside the DCF JSON (e.g. owner FCF history)."""
    table = dcf_get(dcf, path)
    if not isinstance(table, dict):
        return [None] * len(rows)
    canon = {periods.canonical(periods.parse(k)): F.num(v) for k, v in table.items()}
    return [canon.get(periods.canonical(periods.parse(r["Period"]))) for r in rows]


def _series_values(spec_series: dict[str, Any], rows: list[dict[str, str]],
                   dcf: dict[str, Any] | None) -> list[float | None]:
    if "column" in spec_series:
        return series(rows, spec_series["column"])
    if "derive" in spec_series:
        return derive(rows, spec_series["derive"])
    return dcf_series(rows, dcf, spec_series["dcf_path"])


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------

def _check_column(name: Any, columns: set[str], where: str) -> None:
    if name not in columns:
        raise SpecError(f"{where}: unknown CSV column {name!r}")


def validate_spec(spec: dict[str, Any], columns: set[str]) -> None:
    for key in ("descriptor", "kpis", "sections", "metric_descriptions"):
        if key not in spec:
            raise SpecError(f"spec missing {key!r}")
    for k in spec["kpis"]:
        where = f"kpi {k.get('label')!r}"
        if k.get("format", "money") not in FORMATS:
            raise SpecError(f"{where}: format must be one of {FORMATS}")
        if sum(x in k for x in ("column", "dcf_path", "value")) != 1:
            raise SpecError(f"{where}: needs exactly one of column / dcf_path / value")
        if "column" in k:
            _check_column(k["column"], columns, where)
    seen: set[str] = set()
    for sec in spec["sections"]:
        if "title" not in sec or "charts" not in sec:
            raise SpecError("every section needs title and charts")
        for ch in sec["charts"]:
            cid = ch.get("id")
            if not cid or not ch.get("title") or not ch.get("series"):
                raise SpecError(f"chart {cid!r} needs id, title and series")
            if cid in seen:
                raise SpecError(f"duplicate chart id {cid!r}")
            seen.add(cid)
            if ch.get("help") not in spec["metric_descriptions"]:
                raise SpecError(f"chart {cid!r}: help key {ch.get('help')!r} not in metric_descriptions")
            for s in ch["series"]:
                where = f"chart {cid!r} series {s.get('label')!r}"
                if sum(x in s for x in ("column", "derive", "dcf_path")) != 1:
                    raise SpecError(f"{where}: needs exactly one of column / derive / dcf_path")
                if "column" in s:
                    _check_column(s["column"], columns, where)
                if "derive" in s:
                    op, _, arg = s["derive"].partition(":")
                    if op == "yoy":
                        _check_column(arg, columns, where)
                    elif op == "ratio":
                        for c in arg.split("/"):
                            _check_column(c, columns, where)
                    else:
                        raise SpecError(f"{where}: unknown derive {s['derive']!r}")
                if s.get("kind", "bar") not in KINDS:
                    raise SpecError(f"{where}: kind must be bar or line")


# ---------------------------------------------------------------------------
# Rendering pieces
# ---------------------------------------------------------------------------

def fmt_value(v: float | None, fmt: str, currency: str, units: str) -> str:
    if v is None:
        return "-"
    if fmt == "money":
        return f"{currency}{v:,.1f}{units}"
    if fmt == "pct":
        return f"{v:.1f}%"
    if fmt == "number":
        return f"{v:,.1f}" if v != int(v) else f"{int(v):,}"
    return str(v)


def render_kpis(spec: dict[str, Any], rows: list[dict[str, str]],
                dcf: dict[str, Any] | None) -> str:
    fy = annual_rows(rows)
    latest = fy[-1] if fy else (rows[-1] if rows else {})
    prior = fy[-2] if len(fy) > 1 else None
    currency, units = spec.get("currency", "$"), spec.get("units", "m")
    cards = []
    for k in spec["kpis"]:
        fmt = k.get("format", "money")
        value_text: str
        v: float | None = None
        if "value" in k:
            value_text = html.escape(str(k["value"]))
        else:
            v = num(latest.get(k["column"])) if "column" in k else F.num(dcf_get(dcf, k["dcf_path"]))
            value_text = fmt_value(v, fmt, currency, units)
        change = k.get("change")
        positive = bool(k.get("positive", True))
        change_text = ""
        if change == "yoy":
            pv = num(prior.get(k["column"])) if (prior and "column" in k) else None
            if v is not None and pv:
                yoy = (v - pv) / abs(pv) * 100
                positive = yoy >= 0
                change_text = f"{'+' if yoy >= 0 else ''}{yoy:.1f}% YoY"
        elif change:
            change_text = html.escape(str(change))
        cards.append(
            '    <div class="kpi-card">\n'
            f'        <div class="kpi-value">{value_text}</div>\n'
            f'        <div class="kpi-label">{html.escape(str(k["label"]))}</div>\n'
            + (f'        <div class="kpi-change {"positive" if positive else "negative"}">'
               f'{change_text}</div>\n' if change_text else "")
            + (f'        <div class="kpi-note">{html.escape(str(k["note"]))}</div>\n'
               if k.get("note") else "")
            + "    </div>")
    return "\n".join(cards)


def chart_data(spec: dict[str, Any], rows: list[dict[str, str]],
               dcf: dict[str, Any] | None) -> dict[str, Any]:
    """{canvas id: {annual: {labels, data}, interim?: {kind, labels, data}}}."""
    fy = annual_rows(rows)
    kind = interim_kind(rows)
    inter = interim_rows(rows)
    out: dict[str, Any] = {}
    for sec in spec["sections"]:
        for ch in sec["charts"]:
            entry: dict[str, Any] = {
                "annual": {"labels": [short_label(r["Period"]) for r in fy],
                           "data": [_series_values(s, fy, dcf) for s in ch["series"]]},
            }
            if kind and inter and ch.get("interim", True):
                entry["interim"] = {"kind": kind,
                                    "labels": [short_label(r["Period"]) for r in inter],
                                    "data": [_series_values(s, inter, dcf) for s in ch["series"]]}
            out[ch["id"]] = entry
    return out


def log_allowed(ch: dict[str, Any], data: dict[str, Any]) -> bool:
    """Log axis only for absolute quantities whose every point is positive."""
    if not ch.get("log") or ch.get("percent"):
        return False
    views = [data["annual"]] + ([data["interim"]] if "interim" in data else [])
    return all(v is None or v > 0 for view in views for s in view["data"] for v in s)


def render_sections(spec: dict[str, Any], data: dict[str, Any]) -> str:
    parts = []
    for sec in spec["sections"]:
        parts.append(f'<h2 class="section-title">{html.escape(str(sec["title"]))}</h2>')
        if sec.get("subtitle"):
            parts.append(f'<p class="section-subtitle">{sec["subtitle"]}</p>')
        parts.append('<div class="charts-container">')
        for ch in sec["charts"]:
            cid, d = ch["id"], data[ch["id"]]
            actions = []
            if "interim" in d:
                short = "Q" if d["interim"]["kind"] == "quarterly" else "H1/H2"
                actions.append(f'<button class="view-toggle active" onclick="setPeriodView(\'{cid}\', '
                               f'\'annual\', this)">FY</button>')
                actions.append(f'<button class="view-toggle" onclick="setPeriodView(\'{cid}\', '
                               f'\'interim\', this)">{short}</button>')
            if log_allowed(ch, d):
                actions.append(f'<button class="log-toggle" onclick="toggleLogScale(\'{cid}\', this)" '
                               'title="Toggle logarithmic y-axis">Log</button>')
            actions.append(f'<button class="help-btn" onclick="openModal(\'{ch["help"]}\')">?</button>')
            parts.append(
                '    <div class="chart-card">\n'
                '        <div class="chart-header">\n'
                f'            <h3>{html.escape(str(ch["title"]))}</h3>\n'
                '            <div class="chart-header-actions">\n'
                + "".join(f"                {a}\n" for a in actions)
                + '            </div>\n'
                '        </div>\n'
                f'        <div class="chart-wrapper"><canvas id="{cid}"></canvas></div>\n'
                + (f'        <div class="chart-annotation">{ch["annotation"]}</div>\n'
                   if ch.get("annotation") else "")
                + '    </div>')
        parts.append("</div>")
    return "\n".join(parts)


def _slider_range(dcf: dict[str, Any]) -> dict[str, tuple[float, float, float]]:
    """(min, max, default) per slider, wide enough for every scenario's default."""
    a = dcf.get("assumptions", {})
    base = a.get("base", {})
    growths, waccs, terms = [], [], []
    for sc in ("base", "bull", "bear"):
        s = a.get(sc, {})
        g = s.get("growth_rates")
        growths.append(float(g[0]) if isinstance(g, list) and g else
                       float(F.num(dcf_get(dcf, "historical_growth.selected_growth_rate")) or 0))
        waccs.append(float(s.get("wacc", base.get("wacc", 10))))
        terms.append(float(s.get("terminal_growth", base.get("terminal_growth", 3))))
    return {
        "growth": (min(-5.0, min(growths) - 5), max(30.0, max(growths) + 5), growths[0]),
        "wacc": (min(6.0, min(waccs) - 1), max(18.0, max(waccs) + 1), waccs[0]),
        "terminal": (0.0, max(5.0, max(terms) + 1), terms[0]),
    }


def dcf_anchors(dcf: dict[str, Any]) -> dict[str, dict[str, float | None]]:
    ivs = F.scenario_ivs(dcf)
    entries = F.entry_prices(dcf)
    out: dict[str, dict[str, float | None]] = {}
    for sc in F.SCENARIOS:
        iv_map = ivs.get(sc, {})
        iv = iv_map.get("intrinsic_value") if "intrinsic_value" in iv_map else (
            next(iter(iv_map.values())) if iv_map else None)
        ep_map = entries.get(sc, {})
        ep = ep_map.get("entry_price") if "entry_price" in ep_map else (
            next(iter(ep_map.values())) if ep_map else None)
        out[sc] = {"iv": iv, "entry": ep}
    return out


def _fmt_slider(v: float) -> str:
    return f"{v:g}"


def render_dcf_section(ticker: str, spec: dict[str, Any], dcf: dict[str, Any]) -> str:
    labels = spec.get("dcf", {})
    r = _slider_range(dcf)
    g, w, t = r["growth"], r["wacc"], r["terminal"]
    e = html.escape
    return f"""<h2 class="section-title">DCF Valuation</h2>

<div class="dcf-section">
  <div class="dcf-philosophy" id="dcfPhilosophy"></div>

  <div class="dcf-summary">
    <div class="dcf-card highlight">
      <div class="dcf-label">Prob-Weighted IV</div>
      <div class="dcf-value" id="dcfWeighted">-</div>
      <div class="dcf-change" id="dcfWeightedUpside"></div>
      <div class="dcf-sublabel" id="dcfWeightedSublabel">-</div>
    </div>
    <div class="dcf-card">
      <div class="dcf-label">Intrinsic Value (Base)</div>
      <div class="dcf-value" id="dcfIV">-</div>
      <div class="dcf-change positive" id="dcfUpside"></div>
      <div class="dcf-sublabel">{labels.get("base_sublabel", "Owner FCF &mdash; after SBC &amp; dilution")}</div>
    </div>
    <div class="dcf-card">
      <div class="dcf-label">Entry Price (15% CAGR)</div>
      <div class="dcf-value" id="dcfEntry">-</div>
      <div class="dcf-sublabel">Buy below for a 15% annual return (incl. interim FCF)</div>
    </div>
    <div class="dcf-card">
      <div class="dcf-label">Current Price</div>
      <div class="dcf-value" id="dcfCurrent">-</div>
      <div class="dcf-sublabel">{e(str(labels.get("current_price_note", "")))}</div>
    </div>
  </div>

  <div class="dcf-warning" id="growthWarning" style="display: none;">
    <span class="warning-icon">&#9888;</span>
    <span id="warningText"></span>
  </div>

  <div class="dcf-inputs">
    <h4>DCF Model Inputs</h4>
    <div class="dcf-inputs-grid">
      <div class="dcf-input-card">
        <div class="dcf-input-label">{e(str(labels.get("base_fcf_label", "Base FCF (after SBC)")))}</div>
        <div class="dcf-input-value" id="baseFCF">-</div>
        <div class="dcf-input-note" id="sbcBreakdown">Starting point for projections</div>
      </div>
      <div class="dcf-input-card">
        <div class="dcf-input-label">Shares Outstanding</div>
        <div class="dcf-input-value" id="sharesOut">-</div>
        <div class="dcf-input-note" id="dilutionNote"></div>
      </div>
      <div class="dcf-input-card">
        <div class="dcf-input-label">Net Debt</div>
        <div class="dcf-input-value" id="netDebt">-</div>
        <div class="dcf-input-note">Negative = net cash</div>
      </div>
    </div>
    <div class="fcf-projections">
      <h5>Projected FCF</h5>
      <div class="fcf-projection-table" id="fcfProjectionTable"></div>
    </div>
  </div>

  <!-- step is fine enough to hold every scenario default exactly; a range
       input snaps to its step and 7.5 became 8 on NPH.NZ with step="1". -->
  <div class="slider-hint" id="dcfEngineNote" style="max-width:1400px;margin:0 auto 8px"></div>
  <div class="dcf-controls">
    <div class="slider-group">
      <label>Growth Rate (Yr 1): <span id="growthValue">-</span></label>
      <input type="range" id="growthSlider" min="{_fmt_slider(g[0])}" max="{_fmt_slider(g[1])}" value="{_fmt_slider(g[2])}" step="0.1" oninput="updateDCFDisplay()">
      <div class="slider-hint" id="growthHint">{e(str(labels.get("growth_hint", "Base-case Year-1 growth rate")))}</div>
    </div>
    <div class="slider-group">
      <label>WACC: <span id="waccValue">-</span></label>
      <input type="range" id="waccSlider" min="{_fmt_slider(w[0])}" max="{_fmt_slider(w[1])}" value="{_fmt_slider(w[2])}" step="0.05" oninput="updateDCFDisplay()">
    </div>
    <div class="slider-group">
      <label>Terminal Growth: <span id="terminalValue">-</span></label>
      <input type="range" id="terminalSlider" min="{_fmt_slider(t[0])}" max="{_fmt_slider(t[1])}" value="{_fmt_slider(t[2])}" step="0.1" oninput="updateDCFDisplay()">
    </div>
  </div>

  <div class="scenario-tabs">
    <button class="scenario-tab active" data-scenario="base" onclick="switchScenario('base')">Base Case</button>
    <button class="scenario-tab" data-scenario="bull" onclick="switchScenario('bull')">Bull Case</button>
    <button class="scenario-tab" data-scenario="bear" onclick="switchScenario('bear')">Bear Case</button>
  </div>

  <div class="scenario-details" id="scenarioDetails">
    <h4>Assumptions</h4>
    <div class="scenario-narrative" id="scenarioNarrative"></div>
    <div class="assumption-grid" id="assumptionGrid"></div>
  </div>

  <div class="sensitivity-section">
    <h4>Sensitivity Analysis: Intrinsic Value by WACC &amp; Terminal Growth (Base Case)</h4>
    <table class="sensitivity-table" id="sensitivityMatrix"></table>
  </div>

  <div class="historical-growth">
    <h4>Historical Growth Rates (CAGR)</h4>
    <div class="growth-grid" id="growthGrid"></div>
  </div>

  <div class="dcf-download">
    <a href="{ticker}_DCF_Model.xlsx" class="download-btn" download>
      <span class="download-icon">&#8681;</span> Download Excel Model
    </a>
  </div>
</div>"""


def _js(obj: Any) -> str:
    """JSON that is safe inside a <script> block."""
    return json.dumps(obj, ensure_ascii=False, indent=1).replace("</", "<\\/")


def render(ticker: str, spec: dict[str, Any], csv_text: str,
           analysis: dict[str, Any], dcf: dict[str, Any] | None) -> str:
    cols, rows = read_csv(csv_text)
    validate_spec(spec, set(cols))
    data = chart_data(spec, rows, dcf)
    company = str(analysis.get("company_name") or analysis.get("name") or ticker)
    chart_specs = [
        {k: v for k, v in ch.items() if k in ("id", "type", "series", "fill", "y_title", "y1_title", "percent")}
        for sec in spec["sections"] for ch in sec["charts"]
    ]
    csv_body = csv_text.strip().replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    subs = {
        "@@COMPANY@@": html.escape(company, quote=False),
        "@@COMPANY_HTML@@": html.escape(company, quote=False),
        "@@TICKER@@": html.escape(ticker),
        "@@DESCRIPTOR@@": str(spec["descriptor"]),
        "@@KPI_CARDS@@": render_kpis(spec, rows, dcf),
        "@@SECTIONS@@": render_sections(spec, data),
        "@@DCF_SECTION@@": render_dcf_section(ticker, spec, dcf) if dcf else "",
        "@@CSV@@": csv_body,
        "@@ANALYSIS_JSON@@": _js(analysis),
        "@@DCF_JSON@@": _js(dcf) if dcf else "null",
        "@@DCF_ANCHORS@@": _js(dcf_anchors(dcf)) if dcf else "{}",
        "@@CHART_SPECS@@": _js(chart_specs),
        "@@CHART_DATA@@": _js(data),
        "@@METRIC_DESCRIPTIONS@@": _js(spec["metric_descriptions"]),
    }
    page = TEMPLATE.read_text()
    for k, v in subs.items():
        page = page.replace(k, v)
    return page


# ---------------------------------------------------------------------------
# Headless verification harness
# ---------------------------------------------------------------------------

_DOM_STUB = r"""
const __els = {};
function __el(id) {
    if (!__els[id]) __els[id] = {
        id: id, textContent: '', innerHTML: '', value: '', style: {}, dataset: {},
        classList: { add() {}, remove() {}, toggle() {} },
        getContext() { return {}; }, querySelectorAll() { return []; }
    };
    return __els[id];
}
globalThis.window = globalThis;
globalThis.document = {
    getElementById: __el, querySelector: () => __el('__q'), querySelectorAll: () => [],
    addEventListener() {}, body: { style: {} }
};
globalThis.Chart = class { constructor(ctx, cfg) { this.data = cfg.data; this.options = cfg.options; } update() {} };
"""


def node_harness(script: str) -> str:
    """Wrap the page's inline script in a DOM stub so node can execute it.

    Prints JSON: for each scenario tab the IV / entry / weighted numbers the
    cards show at that scenario's defaults, and the weighted IV after a
    slider move -- the verify gate, without a browser.
    """
    stub = _DOM_STUB
    probe = r"""
const __num = s => parseFloat(String(s).replace(/^[^\d\-.]+/, ''));
const __out = {};
for (const sc of ['base', 'bull', 'bear']) {
    switchScenario(sc);
    const d = scenarioDefaults(sc);
    const s = sliderValues();
    __out[sc] = {
        iv: __num(__el('dcfIV').textContent), entry: __num(__el('dcfEntry').textContent),
        weighted: __num(__el('dcfWeighted').textContent),
        slider_ok: s.growth === d.growth && s.wacc === d.wacc && s.terminal === d.terminal
    };
}
switchScenario('base');
__el('growthSlider').value = String(sliderValues().growth + 5);
updateDCFDisplay();
__out.moved = { weighted: __num(__el('dcfWeighted').textContent), iv: __num(__el('dcfIV').textContent) };
__out.engine = dcfEngineStatus;
console.log(JSON.stringify(__out));
"""
    return stub + script + probe


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build(ticker: str, out: pathlib.Path | None = None) -> pathlib.Path:
    reports = REPO / "research" / ticker / "Reports"
    spec_path = reports / f"{ticker}_DashboardSpec.json"
    if not spec_path.exists():
        raise FileNotFoundError(f"{spec_path} -- the dashboard-generator agent writes the DashboardSpec")
    spec = json.loads(spec_path.read_text())
    csv_text = (reports / f"{ticker}_Metrics.csv").read_text()
    analysis = json.loads((reports / f"{ticker}_Analysis.json").read_text())
    dcf_path = reports / f"{ticker}_DCF.json"
    dcf = json.loads(dcf_path.read_text()) if dcf_path.exists() else None
    page = render(ticker, spec, csv_text, analysis, dcf)
    target = out or reports / f"{ticker}_Dashboard.html"
    target.write_text(page)
    n_charts = sum(len(s["charts"]) for s in spec["sections"])
    msg = f"{target}: {len(page):,} bytes, {len(spec['kpis'])} KPIs, {n_charts} charts"
    if dcf:
        wiv = F.weighted_ivs(dcf)
        base = dcf_anchors(dcf)["base"]["iv"]
        msg += (f"; DCF anchored at base_iv {base} / weighted_iv "
                f"{wiv.get('weighted_iv', next(iter(wiv.values()), None))}")
    else:
        msg += "; no DCF.json -- valuation section omitted"
    print(msg)
    if dcf:
        status = engine_status(page)
        if status:
            cells = " ".join(f"{sc}:{'ok' if status[sc]['ok'] else 'FALLBACK'}" for sc in ("base", "bull", "bear"))
            fam = status["base"].get("family") or "none"
            print(f"slider engine: {fam} {cells}")
            if not all(status[sc]["ok"] for sc in ("base", "bull", "bear")):
                print("  !! the DCF JSON's assumptions do not rebuild its valuation; sliders will use the "
                      "generic scaler. Record growth_rates, ebitda_margin_path, sbc_pct_path, da_pct, "
                      "capex_pct, wc_capture_pct, cash_tax_rate_path, terminal_cap_multiple and "
                      "projections.*.revenue so the dashboard can re-run the model.", file=sys.stderr)
    return target


def engine_status(page: str) -> dict[str, Any] | None:
    """Run the page's inline script headlessly and return dcfEngineStatus,
    or None when node is unavailable."""
    m = re.search(r"<script>(.*)</script>\s*</body>", page, re.DOTALL)
    if not m:
        return None
    with tempfile.TemporaryDirectory() as d:
        js = pathlib.Path(d) / "dash.js"
        js.write_text(node_harness(m.group(1)))
        try:
            r = subprocess.run(["node", str(js)], capture_output=True, text=True, check=False, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
    if r.returncode:
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]).get("engine")
    except (json.JSONDecodeError, IndexError):
        return None


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print("usage: build_dashboard.py TICKER [--out path]", file=sys.stderr)
        return 2
    ticker = argv[0]
    out = pathlib.Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    try:
        build(ticker, out)
    except (SpecError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
