#!/usr/bin/env python3
"""Write a deterministic default DashboardSpec for a ticker.

The dashboard-generator agent cost $1.64 per ticker (14% of a run) and
spent its 28 turns reading OTHER tickers' dashboards to decide which of
about ten standard charts to include. That decision is a function of two
things a script can read: which CSV columns are populated, and what kind
of business it is (sectors.bucket). So the default spec is assembled here
from templates -- a universal set every operating company gets, plus one
business-model section -- with any card or chart whose data is absent
dropped. The agent, if it runs at all, edits this file rather than
authoring one.

Templates are data: scripts/templates/dashboard_specs/{universal,saas,
reit,...}.json, with help text in scripts/templates/metric_descriptions.json
({Company} is substituted). The builder's own validate_spec is the gate.

Usage:
  dashboard_spec.py TICKER            # writes Reports/{T}_DashboardSpec.json
  dashboard_spec.py TICKER --stdout   # print, write nothing
  dashboard_spec.py TICKER --force    # overwrite an existing (agent-edited) spec
"""

import collections
import json
import pathlib
import sys
from collections.abc import Callable
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import build_dashboard as BD
import sectors

REPO = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = pathlib.Path(__file__).resolve().parent / "templates" / "dashboard_specs"
DESCRIPTIONS = pathlib.Path(__file__).resolve().parent / "templates" / "metric_descriptions.json"

MIN_POINTS = 3          # a series needs this many populated rows to earn a chart
MAX_KPIS = 8

PENCE_CODES = ("GBp", "GBX", "ZAc")
CURRENCY_SYMBOLS = {
    "USD": "$", "NZD": "NZ$", "AUD": "A$", "EUR": "€", "GBP": "£", "GBX": "p",
    "HKD": "HK$", "JPY": "¥", "CNY": "RMB", "RMB": "RMB", "CAD": "C$", "SEK": "kr",
    "DKK": "kr", "NOK": "kr", "CHF": "CHF", "KZT": "₸", "SGD": "S$", "INR": "₹",
    "KRW": "₩", "TWD": "NT$", "BRL": "R$", "ZAR": "R", "PLN": "zł",
}
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def load_templates() -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(TEMPLATES.glob("*.json"))}


def load_descriptions() -> dict[str, dict[str, str]]:
    return json.loads(DESCRIPTIONS.read_text())


def _series_columns(s: dict[str, Any]) -> list[str]:
    if "column" in s:
        return [s["column"]]
    if "derive" in s:
        _, _, arg = s["derive"].partition(":")
        return arg.split("/")
    return []


def template_columns(tpl: dict[str, Any]) -> set[str]:
    """Every CSV column a template refers to."""
    out: set[str] = set()
    for k in tpl.get("kpis", []):
        if "column" in k:
            out.add(k["column"])
    for sec in tpl.get("sections", []):
        for ch in sec.get("charts", []):
            for s in ch.get("series", []):
                out.update(_series_columns(s))
    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _keep_series(s: dict[str, Any], has: Callable[[str], bool],
                 dcf: dict[str, Any] | None) -> bool:
    if "dcf_path" in s:
        v = BD.dcf_get(dcf, s["dcf_path"])
        return bool(v) if isinstance(v, dict) else v is not None
    return all(has(c) for c in _series_columns(s))


def _keep_kpi(k: dict[str, Any], has_latest: Callable[[str], bool],
              dcf: dict[str, Any] | None) -> bool:
    if "value" in k:
        return True
    if "dcf_path" in k:
        return BD.dcf_get(dcf, k["dcf_path"]) is not None
    return has_latest(k["column"])


def assemble(universal: dict[str, Any], business: dict[str, Any], columns: set[str],
             *, has: Callable[[str], bool], company: str, dcf: dict[str, Any] | None,
             has_latest: Callable[[str], bool] | None = None,
             descriptions: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Merge the universal and business templates, dropping what has no data.

    `has(col)` says a column has enough points to chart; `has_latest(col)`
    says the latest fiscal row has a value (for cards). Business cards and
    charts come first: they tell this company's story.
    """
    has_latest = has_latest or has
    descriptions = descriptions or load_descriptions()

    kpis: list[dict[str, Any]] = []
    for src in (business, universal):
        for k in src.get("kpis", []):
            if ((k.get("column") in columns or "column" not in k)
                    and _keep_kpi(k, has_latest, dcf) and len(kpis) < MAX_KPIS):
                kpis.append(dict(k))

    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for src in (universal, business):
        for sec in src.get("sections", []):
            charts = []
            for ch in sec.get("charts", []):
                if ch["id"] in seen:
                    continue
                series = [dict(s) for s in ch["series"]
                          if all(c in columns for c in _series_columns(s))
                          and _keep_series(s, has, dcf)]
                if not series or not any("column" in s or "derive" in s for s in series):
                    continue
                kept = {**ch, "series": series}
                if not any(s.get("axis") == "y1" for s in series):
                    kept.pop("y1_title", None)
                charts.append(kept)
                seen.add(ch["id"])
            if charts:
                sections.append({**sec, "charts": charts})

    used = {ch["help"] for sec in sections for ch in sec["charts"]}
    md = {}
    for key in sorted(used):
        entry = descriptions.get(key) or {"title": key, "content": f"<p>{key}</p>"}
        md[key] = {k: v.replace("{Company}", company) for k, v in entry.items()}

    return {"descriptor": "", "currency": "$", "units": "m", "kpis": kpis,
            "sections": sections, "metric_descriptions": md, "dcf": {}}


# ---------------------------------------------------------------------------
# Inputs for one ticker
# ---------------------------------------------------------------------------

def _load_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return d if isinstance(d, dict) else None


def _mode(rows: list[dict[str, str]], column: str) -> str | None:
    vals = collections.Counter((r.get(column) or "").strip() for r in rows)
    vals.pop("", None)
    return vals.most_common(1)[0][0] if vals else None


def _fy_end(info: dict[str, Any] | None) -> str | None:
    """'06-30' -> '30 June'; a month name passes through."""
    raw = (info or {}).get("fiscal_year_end")
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    if len(raw) == 5 and raw[2] == "-" and raw[:2].isdigit() and raw[3:].isdigit():
        m, d = int(raw[:2]), int(raw[3:])
        if 1 <= m <= 12:
            return f"{d} {MONTHS[m - 1]}"
    return raw


def default_spec(ticker: str, repo: pathlib.Path | None = None) -> dict[str, Any]:
    repo = repo or REPO
    reports = repo / "research" / ticker / "Reports"
    csv_path = reports / f"{ticker}_Metrics.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"no metrics CSV at {csv_path}")
    cols, rows = BD.read_csv(csv_path.read_text())
    analysis = _load_json(reports / f"{ticker}_Analysis.json") or {}
    info = _load_json(repo / "research" / ticker / "info.json") or {}
    dcf = _load_json(reports / f"{ticker}_DCF.json")

    company = str(analysis.get("company_name") or info.get("name") or ticker)
    sector = str(info.get("sector") or analysis.get("sector") or "").strip()
    if sector.lower() in ("none", "unknown"):
        sector = ""
    which = sectors.bucket(sector or None, sectors.dcf_model_label(dcf, include_prose=False))
    templates = load_templates()
    business = templates.get(which, templates["operating"])

    fy = BD.annual_rows(rows)
    latest = fy[-1] if fy else (rows[-1] if rows else {})
    columns = set(cols)

    def has(col: str) -> bool:
        return col in columns and sum(1 for r in rows if BD.num(r.get(col)) is not None) >= MIN_POINTS

    def has_latest(col: str) -> bool:
        return col in columns and BD.num(latest.get(col)) is not None

    spec = assemble(templates["universal"], business, columns, has=has, company=company,
                    dcf=dcf, has_latest=has_latest)

    raw_inputs = (dcf or {}).get("inputs")
    inputs: dict[str, Any] = raw_inputs if isinstance(raw_inputs, dict) else {}
    ccy = str(inputs.get("currency") or (dcf or {}).get("currency") or _mode(rows, "Currency") or "").upper()
    quote_raw = str(inputs.get("quote_currency") or "")
    # Minor-unit codes are case-sensitive: GBp is pence, GBP is pounds.
    quote = f"{quote_raw} (pence)" if quote_raw in PENCE_CODES else quote_raw.upper()
    units_raw = (_mode(rows, "Units") or "millions").lower()
    units = {"thousands": "k", "billions": "bn"}.get(units_raw, "m")

    parts = [sector or "Operating company"]
    if fy_end := _fy_end(info):
        parts.append(f"FY ends {fy_end}")
    if ccy:
        parts.append(f"reported in {ccy}")
    if quote and ccy and quote != ccy:
        parts.append(f"quoted in {quote}")
    spec["descriptor"] = " &middot; ".join(parts)
    spec["currency"] = CURRENCY_SYMBOLS.get(ccy, f"{ccy} " if ccy else "$")
    spec["units"] = units

    latest_label = str(latest.get("Period") or "latest FY")
    dcf_block: dict[str, Any] = {
        "base_fcf_label": f"Base Owner FCF ({latest_label})",
        "growth_hint": "Base-case Year-1 revenue growth",
        "base_sublabel": "Owner FCF &mdash; after SBC, interest income &amp; leases",
    }
    if dcf:
        asof = (dcf.get("price_refresh") or {}).get("refreshed_at") or dcf.get("valuation_date")
        if asof:
            dcf_block["current_price_note"] = f"as of {str(asof)[:10]}"
        sc = dcf.get("sanity_check")
        if isinstance(sc, dict) and sc.get("fix_applied"):
            dcf_block["model_approach_note"] = str(sc["fix_applied"])
    spec["dcf"] = dcf_block
    spec["bucket"] = which

    BD.validate_spec(spec, columns)
    return spec


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    ticker = argv[0]
    try:
        spec = default_spec(ticker, REPO)
    except (FileNotFoundError, BD.SpecError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    text = json.dumps(spec, indent=1, ensure_ascii=False) + "\n"
    if "--stdout" in argv:
        print(text)
        return 0
    out = REPO / "research" / ticker / "Reports" / f"{ticker}_DashboardSpec.json"
    if out.exists() and "--force" not in argv:
        print(f"{out.name} exists (an agent may have edited it); re-run with --force "
              "to replace it with the default", file=sys.stderr)
        return 1
    out.write_text(text)
    n_charts = sum(len(s["charts"]) for s in spec["sections"])
    print(f"{ticker}: {spec['bucket']} template -> {out.name} "
          f"({len(spec['kpis'])} KPIs, {n_charts} charts, {len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
