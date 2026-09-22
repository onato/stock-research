#!/usr/bin/env python3
"""Build state/research.duckdb, the cross-ticker database, from the committed files.

    python3 scripts/build_warehouse.py            # make warehouse
    duckdb state/research.duckdb                  # then query it

The per-ticker .duckdb files are caches of one extraction; the committed
files under research/*/Reports/ are the record. This reads only those --
{T}_Metrics.csv, {T}_DCF.json (and {T}_Drivers.json), {T}_Analysis.json,
{T}_Prices.csv, {T}_Corrections.jsonl, info.json, evals/ledger.jsonl -- and
rebuilds the whole database from scratch on every run, atomically. A schema
change is a rebuild, never a migration, and every row traces to a file.

Tables: companies, metrics (+ metrics_m view in millions, latest_annual),
kpis, dcf, dcf_scenarios, dcf_values (every numeric key, lossless; scenario
'all' for block-level keys),
dcf_drivers (one row per scenario-year), analysis, prices, ledger,
corrections, build_info, and the `screen` view joining them.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

import dcf_engine
import dcf_fields as F
import duckdb
import periods
import sanity_check
import schema

REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "state" / "research.duckdb"
DEFAULT_LEDGER = REPO / "evals" / "ledger.jsonl"
SCENARIOS = ("bear", "base", "bull")

MONEY = schema.MONEY_COLUMNS
CORE_NUMERIC = [n for n, t, _ in schema.CORE_COLUMNS if t == "DOUBLE"]

DDL = f"""
CREATE TABLE companies (
  ticker TEXT PRIMARY KEY, name TEXT, sector TEXT, exchange TEXT, fiscal_year_end TEXT,
  updated_at TEXT, needs_review BOOLEAN,
  has_metrics BOOLEAN, has_dcf BOOLEAN, has_drivers BOOLEAN, has_analysis BOOLEAN, has_prices BOOLEAN
);
CREATE TABLE metrics (
  ticker TEXT, period TEXT, period_type TEXT, fiscal_year INTEGER, months INTEGER, is_annual BOOLEAN,
  {", ".join(f"{n} DOUBLE" for n in CORE_NUMERIC)},
  units TEXT, currency TEXT, source_file TEXT
);
CREATE TABLE kpis (ticker TEXT, period TEXT, name TEXT, value DOUBLE);
CREATE TABLE dcf (
  ticker TEXT PRIMARY KEY, valuation_date TEXT, current_price DOUBLE, model TEXT, model_family TEXT,
  currency TEXT, quote_currency TEXT, fx_rate DOUBLE,
  weighted_iv DOUBLE, street_weighted_iv DOUBLE, weighted_upside_pct DOUBLE, weighted_entry_price DOUBLE,
  hurdle_rate DOUBLE, band_position DOUBLE,
  weight_bear DOUBLE, weight_base DOUBLE, weight_bull DOUBLE,
  sanity_passed BOOLEAN, engine_version INTEGER, has_drivers BOOLEAN, file_mtime TEXT, doc JSON
);
CREATE TABLE dcf_scenarios (
  ticker TEXT, scenario TEXT, intrinsic_value DOUBLE, street_intrinsic_value DOUBLE, upside_pct DOUBLE,
  entry_price DOUBLE, weight DOUBLE, wacc DOUBLE, terminal_growth DOUBLE, terminal_cap_multiple DOUBLE,
  terminal_value_bound TEXT, terminal_pct_of_value DOUBLE, engine_check TEXT
);
CREATE TABLE dcf_values (ticker TEXT, block TEXT, scenario TEXT, key TEXT, value DOUBLE);
CREATE TABLE dcf_drivers (
  ticker TEXT, scenario TEXT, year_index INTEGER, year_label TEXT,
  growth_pct DOUBLE, ebitda_margin_pct DOUBLE, sbc_pct DOUBLE, lease_pct DOUBLE, da_pct DOUBLE,
  capex_pct DOUBLE, cash_tax_rate_pct DOUBLE, revenue DOUBLE, fcf DOUBLE
);
CREATE TABLE analysis (ticker TEXT PRIMARY KEY, company_name TEXT, doc JSON);
CREATE TABLE prices (ticker TEXT, date DATE, close DOUBLE);
CREATE TABLE ledger (
  ticker TEXT, logged_at TEXT, valuation_date TEXT, current_price DOUBLE, weighted_iv DOUBLE,
  weighted_upside DOUBLE, valuation_model TEXT, git_head TEXT
);
CREATE TABLE corrections (
  ticker TEXT, ts TEXT, target TEXT, period TEXT, col TEXT, op TEXT,
  old_value DOUBLE, new_value DOUBLE, unit TEXT, source TEXT, actor TEXT
);
CREATE TABLE build_info (
  built_at TEXT, git_head TEXT, research_dir TEXT, tickers INTEGER, metrics_rows INTEGER, dcf_rows INTEGER
);
"""

VIEWS = f"""
CREATE VIEW metrics_m AS
WITH scaled AS (
  SELECT *,
    CASE lower(units)
      WHEN 'absolute' THEN 1e-6 WHEN 'absolute dollars' THEN 1e-6 WHEN 'units' THEN 1e-6
      WHEN 'thousands' THEN 1e-3 WHEN 'millions' THEN 1.0 WHEN 'billions' THEN 1e3
      ELSE NULL      -- unrecorded units are NULL, never an assumed scale (SEK.NZ)
    END AS k
  FROM metrics)
SELECT ticker, period, period_type, fiscal_year, months, is_annual, currency, units AS units_raw,
  {", ".join(f"{n} * k AS {n}" for n in MONEY)},
  abs(capex) * k AS capex_abs,
  shares_outstanding * k AS shares_outstanding,
  eps, dividend_per_share, gross_margin, operating_margin, net_margin
FROM scaled;

CREATE VIEW latest_annual AS
SELECT m.* FROM metrics_m m
JOIN (SELECT ticker, max(fiscal_year) AS fy FROM metrics_m WHERE is_annual GROUP BY ticker) l
  ON l.ticker = m.ticker AND l.fy = m.fiscal_year AND m.is_annual;

CREATE VIEW screen AS
SELECT c.ticker, c.name, c.sector, c.exchange,
  a.period AS latest_period, a.revenue AS revenue_m, a.net_income AS net_income_m,
  a.free_cash_flow AS fcf_m, a.eps, a.currency AS reporting_currency,
  d.valuation_date, d.current_price, d.quote_currency, d.model_family,
  d.weighted_iv, d.weighted_upside_pct AS upside_pct, d.weighted_entry_price, d.has_drivers
FROM companies c
LEFT JOIN latest_annual a ON a.ticker = c.ticker
LEFT JOIN dcf d ON d.ticker = c.ticker
WHERE c.has_metrics OR c.has_dcf;
"""


def _num(v: Any) -> float | None:
    return F.num(v)


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _git_head() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                           check=False, timeout=10, cwd=REPO)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() or None


def _mtime(p: pathlib.Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime, dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Readers, one per committed file kind
# ---------------------------------------------------------------------------
def read_metrics(ticker: str, path: pathlib.Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    rows: list[tuple[Any, ...]] = []
    kpis: list[tuple[Any, ...]] = []
    with path.open(newline="") as f:
        for raw in csv.DictReader(f):
            period = (raw.get("Period") or "").strip()
            if not period:
                continue
            p = periods.parse(period)
            core: dict[str, Any] = {}
            for h, v in raw.items():
                if h is None or h == "Period":
                    continue
                col = schema.normalize(h)
                if col in ("units", "currency"):
                    core[col] = (v or "").strip() or None
                elif col:
                    core[col] = _num(v)
                else:
                    n = _num(v)
                    if n is not None:
                        kpis.append((ticker, period, h, n))
            rows.append((ticker, period, p.ptype, p.fiscal_year, p.months, periods.is_annual(p),
                         *[core.get(n) for n in CORE_NUMERIC], core.get("units"), core.get("currency"),
                         path.name))
    return rows, kpis


def _numeric_leaves(block: Any) -> list[tuple[str, str, float]]:
    """(scenario, key, value) for every numeric leaf: scenario dicts one
    level down, everything else at scenario 'all'."""
    out: list[tuple[str, str, float]] = []
    if not isinstance(block, dict):
        return out
    for k, v in block.items():
        if isinstance(v, dict) and k in SCENARIOS:
            out.extend((k, kk, n) for kk, vv in v.items() if (n := _num(vv)) is not None and not isinstance(vv, bool))
        elif not isinstance(v, (dict, list, bool)) and (n := _num(v)) is not None:
            out.append(("all", k, n))
    return out


def read_dcf(ticker: str, path: pathlib.Path, has_drivers: bool) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(doc, dict):
        return None
    inputs = _dict(doc.get("inputs"))
    pw = _dict(doc.get("probability_weighted"))
    weights = F.weights(doc)
    wivs = F.weighted_ivs(doc)
    ups = F.weighted_upsides(doc)
    entry = F.entry_prices(doc)
    sanity = _dict(doc.get("sanity_check"))
    engine = _dict(doc.get("engine"))
    checks = dcf_engine.check(doc)
    summary = (
        ticker, doc.get("valuation_date"), _num(doc.get("current_price")), _model_label(doc),
        sanity_check.model_family(doc),
        inputs.get("currency"), inputs.get("quote_currency"), _num(inputs.get("fx_rate")),
        wivs.get("weighted_iv", next(iter(wivs.values()), None)),
        _num(pw.get("street_weighted_iv")),
        ups.get("weighted_upside_pct", next(iter(ups.values()), None)),
        entry.get("_flat", {}).get("weighted_entry_price"),
        F.hurdle_rate(doc), _num(pw.get("band_position")),
        weights.get("bear"), weights.get("base"), weights.get("bull"),
        sanity.get("passed") if isinstance(sanity.get("passed"), bool) else None,
        int(engine["version"]) if _num(engine.get("version")) is not None else None,
        has_drivers, _mtime(path), json.dumps(doc, ensure_ascii=False),
    )
    scen_block = F.scenario_block(doc)
    assumptions = _dict(doc.get("assumptions"))
    scenarios: list[tuple[Any, ...]] = []
    drivers: list[tuple[Any, ...]] = []
    for sc in SCENARIOS:
        v = _dict(scen_block.get(sc))
        a = _dict(assumptions.get(sc))
        scenarios.append((
            ticker, sc, _num(v.get("intrinsic_value")), _num(v.get("street_intrinsic_value")),
            _num(v.get("upside")), entry.get(sc, {}).get("entry_price"), weights.get(sc),
            _num(a.get("wacc")), _num(a.get("terminal_growth")),
            _num(dcf_engine._first(a, dcf_engine.ALIASES["terminal_cap_multiple"])),
            v.get("terminal_value_bound") if isinstance(v.get("terminal_value_bound"), str) else None,
            _num(v.get("terminal_pct_of_value")), checks.get(sc),
        ))
        drivers.extend(_driver_rows(ticker, sc, a, doc))
    values: list[tuple[Any, ...]] = []
    for block in ("valuation", "entry_price", "probability_weighted"):
        src = scen_block if block == "valuation" else doc.get(block)
        values.extend((ticker, block, sc, k, n) for sc, k, n in _numeric_leaves(src))
    return {"summary": summary, "scenarios": scenarios, "drivers": drivers, "values": values}


def _model_label(doc: dict[str, Any]) -> str | None:
    for k in ("model", "valuation_model", "model_type", "method"):
        if isinstance(doc.get(k), str):
            return doc[k]
    return None


def _driver_rows(ticker: str, sc: str, a: dict[str, Any], doc: dict[str, Any]) -> list[tuple[Any, ...]]:
    g = dcf_engine._first(a, dcf_engine.ALIASES["growth_rates"])
    if isinstance(g, (int, float)) and not isinstance(g, bool):
        g = [float(g)] * int(a.get("horizon_years") or 5)
    if not isinstance(g, list) or not g:
        return []
    try:
        growth = [float(x) for x in g]
    except (TypeError, ValueError):
        return []
    n = len(growth)

    def path(name: str) -> list[float | None]:
        v = dcf_engine._first(a, dcf_engine.ALIASES[name])
        if isinstance(v, list):
            try:
                v = [float(x) for x in v]
            except (TypeError, ValueError):
                return [None] * n
        try:
            p = dcf_engine._as_path(v, n, None)
        except (TypeError, ValueError):
            return [None] * n
        return list(p) if p else [None] * n

    proj = _dict(_dict(doc.get("projections")).get(sc))

    def series(key: str) -> list[float | None]:
        s = proj.get(key)
        if not isinstance(s, list):
            return [None] * n
        return [_num(x) for x in s[:n]] + [None] * max(0, n - len(s))

    raw_years = proj.get("years")
    years: list[Any] = raw_years if isinstance(raw_years, list) else []
    cols = [path(k) for k in ("ebitda_margin_path", "sbc_pct_path", "lease_cost_pct_path", "da_pct_path",
                              "capex_pct_path", "cash_tax_rate_path")]
    rev, fcf = series("revenue"), series("fcf")
    return [(ticker, sc, i + 1, str(years[i]) if i < len(years) else None, growth[i],
             *[c[i] for c in cols], rev[i], fcf[i]) for i in range(n)]


def read_prices(ticker: str, path: pathlib.Path) -> list[tuple[Any, ...]]:
    out = []
    with path.open(newline="") as f:
        for raw in csv.DictReader(f):
            d, c = (raw.get("Date") or "").strip(), _num(raw.get("Close"))
            if d and c is not None:
                out.append((ticker, d, c))
    return out


def read_corrections(ticker: str, path: pathlib.Path) -> list[tuple[Any, ...]]:
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            out.append((ticker, r.get("ts"), r.get("target"), r.get("period"), r.get("col"), r.get("op"),
                        _num(r.get("old_value")), _num(r.get("new_value")), r.get("unit"), r.get("source"),
                        r.get("actor")))
    return out


def read_ledger(path: pathlib.Path) -> list[tuple[Any, ...]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(r, dict):
            continue

        def pick(v: Any, prefer: str) -> float | None:
            if isinstance(v, dict):
                return _num(v.get(prefer, next(iter(v.values()), None)))
            return _num(v)

        out.append((r.get("ticker"), r.get("logged_at"), r.get("valuation_date"), _num(r.get("current_price")),
                    pick(r.get("weighted_iv"), "weighted_iv"), pick(r.get("weighted_upside"), "weighted_upside"),
                    r.get("valuation_model"), r.get("git_head")))
    return out


def _load(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple[Any, ...]]) -> None:
    """Bulk-load through a temp CSV. executemany probes for numpy/pandas on
    every parameter and, with neither installed, rescans sys.path each time:
    18.7s for 50k rows against 0.06s for COPY."""
    if not rows:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerows([("" if v is None else v) for v in r] for r in rows)
        path = f.name
    try:
        con.execute(f"COPY {table} FROM '{path}' (FORMAT CSV, HEADER false, NULL '')")
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build(root: pathlib.Path, out: pathlib.Path, research: pathlib.Path | None = None,
          ledger: pathlib.Path | None = None) -> dict[str, int]:
    research = research or root / "research"
    ledger = ledger or root / "evals" / "ledger.jsonl"
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute(DDL)

    companies, metrics, kpis, dcfs, scen, drv, vals, analyses, prices, corrections = ([] for _ in range(10))
    for d in sorted(p for p in research.iterdir() if p.is_dir()):
        t = d.name
        info_p = d / "info.json"
        rep = d / "Reports"
        if not info_p.exists() and not rep.is_dir():
            continue
        try:
            info = json.loads(info_p.read_text()) if info_p.exists() else {}
        except (json.JSONDecodeError, OSError):
            info = {}
        if not isinstance(info, dict):
            info = {}
        files = {k: rep / f"{t}_{k}" for k in ("Metrics.csv", "DCF.json", "Drivers.json", "Analysis.json",
                                              "Prices.csv", "Corrections.jsonl")}
        have = {k: p.exists() for k, p in files.items()}
        companies.append((t, info.get("name"), info.get("sector"), t.rsplit(".", 1)[1] if "." in t else None,
                          info.get("fiscal_year_end"), info.get("updated_at"),
                          info.get("needs_review") if isinstance(info.get("needs_review"), bool) else None,
                          have["Metrics.csv"], have["DCF.json"], have["Drivers.json"], have["Analysis.json"],
                          have["Prices.csv"]))
        if have["Metrics.csv"]:
            m, k = read_metrics(t, files["Metrics.csv"])
            metrics.extend(m)
            kpis.extend(k)
        if have["DCF.json"]:
            r = read_dcf(t, files["DCF.json"], have["Drivers.json"])
            if r:
                dcfs.append(r["summary"])
                scen.extend(r["scenarios"])
                drv.extend(r["drivers"])
                vals.extend(r["values"])
        if have["Analysis.json"]:
            try:
                a = json.loads(files["Analysis.json"].read_text())
                if isinstance(a, dict):
                    analyses.append((t, a.get("company_name") or a.get("name"), json.dumps(a, ensure_ascii=False)))
            except (json.JSONDecodeError, OSError):
                pass
        if have["Prices.csv"]:
            prices.extend(read_prices(t, files["Prices.csv"]))
        if have["Corrections.jsonl"]:
            corrections.extend(read_corrections(t, files["Corrections.jsonl"]))

    ledger_rows = read_ledger(ledger)
    tables = {
        "companies": companies, "metrics": metrics, "kpis": kpis, "dcf": dcfs, "dcf_scenarios": scen,
        "dcf_values": vals, "dcf_drivers": drv, "analysis": analyses, "prices": prices, "ledger": ledger_rows,
        "corrections": corrections,
    }
    for name, rows in tables.items():
        _load(con, name, rows)
    con.execute("INSERT INTO build_info VALUES (?, ?, ?, ?, ?, ?)", (
        dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"), _git_head(),
        str(research), len(companies), len(metrics), len(dcfs)))
    con.execute(VIEWS)
    con.close()
    os.replace(tmp, out)
    return {k: len(v) for k, v in tables.items()}


def main(argv: list[str]) -> int:
    research = REPO / "research"
    out = DEFAULT_OUT
    ledger = DEFAULT_LEDGER
    if "--research" in argv:
        research = pathlib.Path(argv[argv.index("--research") + 1])
    if "--out" in argv:
        out = pathlib.Path(argv[argv.index("--out") + 1])
    if "--ledger" in argv:
        ledger = pathlib.Path(argv[argv.index("--ledger") + 1])
    counts = build(REPO, out, research=research, ledger=ledger)
    print(f"{out}:")
    for k, v in counts.items():
        print(f"  {k} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
