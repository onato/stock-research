#!/usr/bin/env python3
"""The owner-FCF component DCF, as a script: {TICKER}_Drivers.json -> DCF.json.

The dcf-analyst's judgment is the driver paths -- growth, margin, SBC, D&A,
capex, cash tax, WACC, terminal growth and cap, scenario weights, the equity
bridge. Turning those into projections, terminal values, entry prices, the
required-return table, the sensitivity grid and the weighted IV is
arithmetic, and the dashboard already re-runs exactly that arithmetic in JS
(`runEngine` in scripts/templates/dashboard.html) to validate every JSON.
Until 2026-09-22 the agent nevertheless wrote a bespoke ~48k-char Python
model per ticker to do it, on the most expensive model in the pipeline.

This module is the Python port of the JS engine. `build(drivers)` returns the
full DCF.json document; `check(dcf)` re-derives any DCF.json from its own
recorded assumptions and reports per-scenario agreement, which is the same
gate `build_dashboard.py` applies with node.

Conventions (the dashboard's): every path is in PERCENT; WACC and terminal
growth are percents; `entry_price.hurdle_rate` is a FRACTION (0.15);
end-of-year discounting; TV = max(0, min(Gordon, cap x FCF_N)) rebuilt at
whichever rate is discounting; working-capital capture is on the revenue
CHANGE; a loss year pays no tax; per-share on the final projected share
count. Money is in millions of the model currency (`inputs.currency`); the
headline per-share figures are in the quote currency via `inputs.fx_rate`.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

SCENARIOS = ("bear", "base", "bull")
ENGINE_TOL = 0.015                 # dashboard.html ENGINE_TOL
DEFAULT_CAP = 20.0
DEFAULT_HURDLE = 0.15
REQUIRED_RETURNS = [9, 10, 11, 12, 15]

# Canonical driver names. The dashboard accepts several spellings of each
# (see ALIASES); the Drivers contract accepts one, so a typo is refused
# instead of silently becoming a no-op default.
PATH_DRIVERS = ("growth_rates", "ebitda_margin_path", "sbc_pct_path", "lease_cost_pct_path",
                "da_pct_path", "capex_pct_path", "cash_tax_rate_path")
SCALAR_DRIVERS = ("wc_capture_pct", "wacc", "terminal_growth", "terminal_cap_multiple",
                  "horizon_years")
ANNOTATIONS = ("narrative", "note", "notes", "rationale")
ALIASES: dict[str, tuple[str, ...]] = {
    "growth_rates": ("growth_rates", "revenue_growth_pct_path", "revenue_growth_rates",
                     "revenue_growth_pct", "growth_rate"),
    "ebitda_margin_path": ("ebitda_margin_path", "ebitda_margin_path_pct", "ebitda_margin"),
    "sbc_pct_path": ("sbc_pct_path", "sbc_pct"),
    "lease_cost_pct_path": ("lease_cost_pct", "lease_cost_pct_path", "lease_principal_pct_path",
                            "lease_principal_pct"),
    "da_pct_path": ("da_pct_path", "da_pct"),
    "capex_pct_path": ("capex_pct_path", "capex_pct"),
    "cash_tax_rate_path": ("cash_tax_rate_path", "cash_tax_rate_path_pct", "cash_tax_rate",
                           "tax_rate"),
    "terminal_cap_multiple": ("terminal_cap_multiple", "terminal_fcf_multiple_cap",
                              "terminal_multiple_cap"),
}
SIDE_STREAM_KEYS = ("merchant_float_value", "float_value", "side_stream_value",
                    "non_operating_assets")


class DriversError(ValueError):
    """The Drivers.json does not carry what the engine needs; the message
    names every missing or malformed field."""


@dataclass(frozen=True)
class TerminalValue:
    value: float
    bound: str          # "gordon" | "cap" | "floor"
    gordon: float
    cap_value: float


def _r(x: float, n: int) -> float:
    """Round half up on the decimal repr, so 6.05 -> 6.1 like a spreadsheet,
    not 6.0 like binary round()."""
    q = Decimal(1).scaleb(-n)
    return float(Decimal(repr(float(x))).quantize(q, rounding=ROUND_HALF_UP))


def _rl(xs: list[float], n: int) -> list[float]:
    return [_r(x, n) for x in xs]


def _first(a: dict[str, Any], names: tuple[str, ...]) -> Any:
    for k in names:
        if a.get(k) is not None:
            return a[k]
    return None


def _as_path(v: Any, n: int, default: float | None = 0.0) -> list[float] | None:
    """The dashboard's asPath(): a list is truncated or padded with its last
    value; a scalar is repeated; None is the default repeated (or None)."""
    if isinstance(v, list) and v:
        out = [float(x) for x in v[:n]]
        while len(out) < n:
            out.append(out[-1])
        return out
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return [float(v)] * n
    return None if default is None else [default] * n


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------
def _project_raw(base_revenue: float, drivers: dict[str, Any]) -> dict[str, list[float]]:
    g = _first(drivers, ALIASES["growth_rates"])
    if isinstance(g, (int, float)):
        g = [float(g)] * int(drivers.get("horizon_years") or 5)
    if not isinstance(g, list) or not g:
        raise DriversError("growth_rates: a non-empty list of percents is required")
    growth = [float(x) for x in g]
    n = len(growth)
    m = _as_path(_first(drivers, ALIASES["ebitda_margin_path"]), n, None)
    if m is None:
        raise DriversError("ebitda_margin_path is required")
    sbc = _as_path(_first(drivers, ALIASES["sbc_pct_path"]), n) or []
    lease = _as_path(_first(drivers, ALIASES["lease_cost_pct_path"]), n) or []
    da = _as_path(_first(drivers, ALIASES["da_pct_path"]), n) or []
    capex = _as_path(_first(drivers, ALIASES["capex_pct_path"]), n) or []
    tax = _as_path(_first(drivers, ALIASES["cash_tax_rate_path"]), n) or []
    wc = float(drivers.get("wc_capture_pct") or 0.0)

    out: dict[str, list[float]] = {k: [] for k in (
        "growth_rates", "revenue", "adj_ebitda", "sbc", "lease_cost", "d_and_a", "ebit",
        "cash_tax_rate", "cash_tax", "nopat", "capex", "working_capital", "fcf",
        "owner_fcf_margin")}
    rev = float(base_revenue)
    for i in range(n):
        prev = rev
        rev *= 1 + growth[i] / 100
        ebitda = rev * m[i] / 100
        sbc_i, lease_i, da_i = rev * sbc[i] / 100, rev * lease[i] / 100, rev * da[i] / 100
        ebit = ebitda - sbc_i - lease_i - da_i
        cash_tax = max(0.0, ebit) * tax[i] / 100
        nopat = ebit - cash_tax
        capex_i = rev * capex[i] / 100
        wc_i = (rev - prev) * wc / 100
        fcf = nopat + da_i - capex_i + wc_i
        for k, v in (("growth_rates", growth[i]), ("revenue", rev), ("adj_ebitda", ebitda),
                     ("sbc", sbc_i), ("lease_cost", lease_i), ("d_and_a", da_i), ("ebit", ebit),
                     ("cash_tax_rate", tax[i]), ("cash_tax", cash_tax), ("nopat", nopat),
                     ("capex", capex_i), ("working_capital", wc_i), ("fcf", fcf),
                     ("owner_fcf_margin", fcf / rev * 100 if rev else 0.0)):
            out[k].append(v)
    return out


def project(base_revenue: float, drivers: dict[str, Any]) -> dict[str, list[float]]:
    """The component build, rounded for display (millions to 1dp, percents
    to 1dp). `build` works from the unrounded series."""
    raw = _project_raw(base_revenue, drivers)
    return {k: _rl(v, 3 if k in ("growth_rates", "cash_tax_rate") else 1) for k, v in raw.items()}


# ---------------------------------------------------------------------------
# Discounting
# ---------------------------------------------------------------------------
def terminal_value(last_fcf: float, rate: float, growth: float, cap: float) -> TerminalValue:
    """max(0, min(Gordon, cap x FCF_N)); a spread at or below zero goes
    straight to the cap. Rebuilt at whichever rate is discounting."""
    cap_value = last_fcf * cap
    if rate - growth <= 0:
        return TerminalValue(max(0.0, cap_value), "cap" if cap_value > 0 else "floor", cap_value, cap_value)
    gordon = last_fcf * (1 + growth / 100) / ((rate - growth) / 100)
    if gordon <= 0 and cap_value <= 0:
        return TerminalValue(0.0, "floor", gordon, cap_value)
    if gordon <= cap_value:
        return TerminalValue(max(0.0, gordon), "gordon" if gordon > 0 else "floor", gordon, cap_value)
    return TerminalValue(max(0.0, cap_value), "cap" if cap_value > 0 else "floor", gordon, cap_value)


def _discount_factors(rate: float, n: int) -> list[float]:
    return [1 / (1 + rate / 100) ** (i + 1) for i in range(n)]


def _pv(fcfs: list[float], rate: float) -> float:
    return sum(f * d for f, d in zip(fcfs, _discount_factors(rate, len(fcfs)), strict=True))


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------
def _validate(drivers: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    inputs = drivers.get("inputs")
    if not isinstance(inputs, dict):
        return ["inputs: object required"]
    errs.extend(f"inputs.{k}: number required"
                for k in ("shares_outstanding", "net_debt", "base_revenue", "last_fcf")
                if not isinstance(inputs.get(k), (int, float)))
    if not isinstance(drivers.get("current_price"), (int, float)):
        errs.append("current_price: number required")
    errs.extend(f"{k}: string required" for k in ("ticker", "valuation_date")
                if not isinstance(drivers.get(k), str) or not drivers[k])
    cur = str(inputs.get("currency") or "").upper()
    quote = str(inputs.get("quote_currency") or cur).upper()
    if cur and quote and cur != quote and not isinstance(inputs.get("fx_rate"), (int, float)):
        errs.append(f"inputs.fx_rate: required when currency ({cur}) differs from quote_currency ({quote})")
    a = drivers.get("assumptions")
    if not isinstance(a, dict):
        errs.append("assumptions: object with bear/base/bull required")
    else:
        for sc in SCENARIOS:
            s = a.get(sc)
            if not isinstance(s, dict):
                errs.append(f"assumptions.{sc}: object required")
                continue
            errs.extend(f"assumptions.{sc}.{k}: non-empty list required"
                        for k in ("growth_rates", "ebitda_margin_path")
                        if not isinstance(s.get(k), list) or not s[k])
            errs.extend(f"assumptions.{sc}.{k}: number required" for k in ("wacc", "terminal_growth")
                        if not isinstance(s.get(k), (int, float)))
            allowed = set(PATH_DRIVERS) | set(SCALAR_DRIVERS) | set(ANNOTATIONS)
            for k in s:
                if k in allowed or k.endswith(("_note", "_notes", "_rationale", "_source")):
                    continue
                errs.append(f"assumptions.{sc}.{k}: unknown driver (canonical names: "
                            f"{', '.join(PATH_DRIVERS + SCALAR_DRIVERS)})")
    pw = drivers.get("probability_weighted")
    w = pw.get("weights") if isinstance(pw, dict) else None
    if not isinstance(w, dict) or any(not isinstance(w.get(sc), (int, float)) for sc in SCENARIOS):
        errs.append("probability_weighted.weights: bear/base/bull numbers required")
    else:
        total = sum(float(w[sc]) for sc in SCENARIOS)
        if abs(total - 1.0) > 0.01:
            errs.append(f"probability_weighted.weights: must sum to 1.0 (got {total:.3f})")
    bridge = drivers.get("equity_bridge", [])
    if not isinstance(bridge, list) or any(
            not isinstance(b, dict) or not isinstance(b.get("value"), (int, float)) for b in bridge):
        errs.append("equity_bridge: list of {item, value[, note]} required")
    return errs


def _year_labels(fy0: str | None, n: int) -> list[str]:
    m = re.search(r"(\d{4})", str(fy0 or ""))
    if not m:
        return [f"Y{i + 1}" for i in range(n)]
    prefix = str(fy0)[:m.start()]
    y0 = int(m.group(1))
    return [f"{prefix}{y0 + i + 1}" for i in range(n)]


def _projected_shares(inputs: dict[str, Any], n: int) -> list[float]:
    ps = inputs.get("projected_shares")
    if isinstance(ps, list) and len(ps) >= n:
        return [float(x) for x in ps[:n]]
    s0 = float(inputs["shares_outstanding"])
    g = float(inputs.get("annual_share_growth_pct") or 0.0) / 100
    return [s0 * (1 + g) ** (i + 1) for i in range(n)]


# ---------------------------------------------------------------------------
# One scenario, end to end
# ---------------------------------------------------------------------------
def street_addback(sbc: list[float], rate: float, growth: float) -> float:
    """owner-fcf.md 'Dual basis': the PV of the projected SBC charge over the
    explicit years plus an (uncapped) Gordon on the terminal-year charge --
    what the house basis expensed and the street treats as non-cash."""
    n = len(sbc)
    explicit = _pv(sbc, rate)
    if rate - growth > 0:
        terminal = sbc[-1] * (1 + growth / 100) / ((rate - growth) / 100)
    else:
        terminal = sbc[-1] * DEFAULT_CAP
    return explicit + terminal / (1 + rate / 100) ** n


@dataclass
class _Scenario:
    raw: dict[str, list[float]]
    wacc: float
    tg: float
    cap: float
    shares: float
    net_debt: float
    bridge: float
    fx: float

    def equity_per_share(self, rate: float, street: bool = False) -> float:
        f = self.raw["fcf"]
        tv = terminal_value(f[-1], rate, self.tg, self.cap)
        ev = _pv(f, rate) + tv.value / (1 + rate / 100) ** len(f)
        add = street_addback(self.raw["sbc"], rate, self.tg) if street else 0.0
        return (ev + add + self.bridge - self.net_debt) / self.shares


def _scenario(drivers: dict[str, Any], sc: str) -> _Scenario:
    inputs = drivers["inputs"]
    a = drivers["assumptions"][sc]
    raw = _project_raw(float(inputs["base_revenue"]), a)
    n = len(raw["fcf"])
    cur = str(inputs.get("currency") or "").upper()
    quote = str(inputs.get("quote_currency") or cur).upper()
    fx = float(inputs["fx_rate"]) if cur and quote and cur != quote else 1.0
    return _Scenario(
        raw=raw, wacc=float(a["wacc"]), tg=float(a["terminal_growth"]),
        cap=float(_first(a, ALIASES["terminal_cap_multiple"]) or DEFAULT_CAP),
        shares=_projected_shares(inputs, n)[-1], net_debt=float(inputs["net_debt"]),
        bridge=sum(float(b["value"]) for b in drivers.get("equity_bridge", [])), fx=fx)


def build(drivers: dict[str, Any]) -> dict[str, Any]:
    """The DCF.json for a Drivers.json. Pass-through blocks are copied as-is;
    computed blocks (projections, valuation, entry_price,
    required_return_table, sensitivity, probability_weighted) are derived."""
    errs = _validate(drivers)
    if errs:
        raise DriversError("; ".join(errs))
    inputs = dict(drivers["inputs"])
    price = float(drivers["current_price"])
    hurdle = float((drivers.get("entry_price") or {}).get("hurdle_rate") or DEFAULT_HURDLE) * 100
    cur = str(inputs.get("currency") or "").upper()
    quote = str(inputs.get("quote_currency") or cur).upper()
    dual = bool(cur and quote and cur != quote)
    sfx = f"_{cur.lower()}" if dual else None
    weights = {sc: float(drivers["probability_weighted"]["weights"][sc]) for sc in SCENARIOS}
    wsum = sum(weights.values())
    weights = {sc: w / wsum for sc, w in weights.items()}

    scen = {sc: _scenario(drivers, sc) for sc in SCENARIOS}
    n = len(scen["base"].raw["fcf"])
    inputs["projected_shares"] = _rl(_projected_shares(inputs, n), 3)
    years = _year_labels(inputs.get("fy0"), n)

    projections: dict[str, Any] = {}
    valuation: dict[str, Any] = {}
    entry: dict[str, Any] = {"hurdle_rate": hurdle / 100}
    assumptions: dict[str, Any] = {}
    iv_model: dict[str, float] = {}
    street_model: dict[str, float] = {}
    entry_model: dict[str, float] = {}
    for sc, s in scen.items():
        fcfs = s.raw["fcf"]
        tv = terminal_value(fcfs[-1], s.wacc, s.tg, s.cap)
        dfs = _discount_factors(s.wacc, n)
        pv_fcf = [f * d for f, d in zip(fcfs, dfs, strict=True)]
        sum_pv = sum(pv_fcf)
        pv_tv = tv.value * dfs[-1]
        ev = sum_pv + pv_tv
        equity = ev + s.bridge - s.net_debt
        iv = equity / s.shares
        street = s.equity_per_share(s.wacc, street=True)
        iv_model[sc], street_model[sc] = iv, street

        a = drivers["assumptions"][sc]
        echoed = {k: a[k] for k in a if k in PATH_DRIVERS or k in SCALAR_DRIVERS or k in ANNOTATIONS
                  or k.endswith(("_note", "_notes", "_rationale", "_source"))}
        echoed.setdefault("sbc_pct_path", [0.0] * n)
        echoed.setdefault("wc_capture_pct", 0.0)
        echoed.setdefault("terminal_cap_multiple", s.cap)
        echoed["horizon_years"] = n
        assumptions[sc] = echoed

        projections[sc] = {"years": years, **{
            k: _rl(v, 3 if k in ("growth_rates", "cash_tax_rate") else 1)
            for k, v in s.raw.items()}}
        projections[sc]["discount_factors"] = _rl(dfs, 4)
        projections[sc]["pv_fcf"] = _rl(pv_fcf, 1)

        v: dict[str, Any] = {
            "sum_pv_fcf": _r(sum_pv, 1), "tv_gordon": _r(tv.gordon, 1), "tv_cap": _r(tv.cap_value, 1),
            "terminal_value": _r(tv.value, 1), "terminal_value_bound": tv.bound,
            "pv_terminal": _r(pv_tv, 1), "enterprise_value": _r(ev, 1),
        }
        if s.bridge:
            v["equity_bridge_total"] = _r(s.bridge, 1)
            v["side_stream_value"] = _r(s.bridge, 1)        # the dashboard engine's name for it
        v.update({
            "net_debt": _r(s.net_debt, 1), "equity_value": _r(equity, 1),
            "intrinsic_value": _r(iv * s.fx, 2), "street_intrinsic_value": _r(street * s.fx, 2),
            "upside": _r((iv * s.fx / price - 1) * 100, 1),
            "terminal_pct_of_value": _r(pv_tv / ev * 100, 1) if ev else None,
            "implied_exit_multiple": _r(tv.value / fcfs[-1], 1) if fcfs[-1] else None,
            "implied_terminal_ev_to_ebitda": (_r(tv.value / s.raw["adj_ebitda"][-1], 1)
                                              if s.raw["adj_ebitda"][-1] else None),
        })
        if sfx:
            v[f"intrinsic_value{sfx}"] = _r(iv, 2)
            v[f"street_intrinsic_value{sfx}"] = _r(street, 2)
        valuation[sc] = v

        tvh = terminal_value(fcfs[-1], hurdle, s.tg, s.cap)
        pv_h = _pv(fcfs, hurdle)
        pv_tvh = tvh.value / (1 + hurdle / 100) ** n
        ep = (pv_h + pv_tvh + s.bridge - s.net_debt) / s.shares
        entry_model[sc] = ep
        e: dict[str, Any] = {
            "years_to_terminal": n, "pv_interim_fcf_at_hurdle": _r(pv_h, 1),
            "terminal_value_at_hurdle": _r(tvh.value, 1), "terminal_value_bound": tvh.bound,
            "pv_terminal_at_hurdle": _r(pv_tvh, 1), "net_debt": _r(s.net_debt, 1),
            "entry_price": _r(ep * s.fx, 2),
            "entry_discount_from_current": _r((ep * s.fx / price - 1) * 100, 1),
        }
        if sfx:
            e[f"entry_price{sfx}"] = _r(ep, 2)
        entry[sc] = e

    fx = scen["base"].fx
    w_entry = sum(weights[sc] * entry_model[sc] for sc in SCENARIOS)
    entry["weighted_entry_price"] = _r(w_entry * fx, 2)
    if sfx:
        entry[f"weighted_entry_price{sfx}"] = _r(w_entry, 2)

    base = scen["base"]
    returns: list[float] = [base.wacc] + [r for r in REQUIRED_RETURNS if r != base.wacc]
    returns = sorted(set(returns), key=float)
    rrt = {
        "scenario": "base",
        "returns": [float(r) if r == base.wacc else r for r in returns],
        "value_per_share": [_r(base.equity_per_share(float(r)) * fx, 2) for r in returns],
        "note": "Each row rebuilds the terminal value at that row's rate "
                f"(max(0, min(Gordon@r, {base.cap:g}x FCF_N))); the WACC row is valuation.base."
                "intrinsic_value and the hurdle row is entry_price.base.entry_price.",
    }
    wacc_range = [base.wacc + d for d in (-2, -1, 0, 1, 2)]
    tg_range = [base.tg + d for d in (-1.0, -0.5, 0.0, 0.5, 1.0)]
    matrix = []
    for w in wacc_range:
        row = []
        for g in tg_range:
            tv = terminal_value(base.raw["fcf"][-1], w, g, base.cap)
            ev = _pv(base.raw["fcf"], w) + tv.value / (1 + w / 100) ** n
            row.append(_r((ev + base.bridge - base.net_debt) / base.shares * fx, 2))
        matrix.append(row)
    sensitivity = {
        "scenario": "base", "wacc_range": _rl(wacc_range, 2), "terminal_growth_range": _rl(tg_range, 2),
        "matrix": matrix, "terminal_cap_multiple": base.cap,
        "note": "Rows are WACC, columns terminal growth; each cell rebuilds its own terminal value.",
    }

    w_iv = sum(weights[sc] * iv_model[sc] for sc in SCENARIOS)
    w_street = sum(weights[sc] * street_model[sc] for sc in SCENARIOS)
    band: float | None = None
    band_note = None
    if w_iv and abs(w_street - w_iv) > 0.01 * abs(w_iv):
        band = _r((price - _r(w_iv * fx, 2)) / (_r(w_street * fx, 2) - _r(w_iv * fx, 2)) * 100, 1)
    else:
        band_note = ("House and street coincide (SBC immaterial): the band collapsed, so "
                     "band_position is null rather than a degenerate 0.")
    pw_in = drivers["probability_weighted"]
    pw: dict[str, Any] = {
        "weights": {sc: _r(weights[sc], 4) for sc in SCENARIOS},
        "weighted_iv": _r(w_iv * fx, 2), "street_weighted_iv": _r(w_street * fx, 2),
        "weighted_upside_pct": _r((w_iv * fx / price - 1) * 100, 1),
        "band_position": band,
    }
    if sfx:
        pw[f"weighted_iv{sfx}"] = _r(w_iv, 2)
        pw[f"street_weighted_iv{sfx}"] = _r(w_street, 2)
    for k, v in pw_in.items():
        if k not in pw:
            pw[k] = v
    if band_note and "band_note" not in pw:
        pw["band_note"] = band_note

    computed = {"inputs", "assumptions", "projections", "valuation", "entry_price",
                "required_return_table", "sensitivity", "probability_weighted", "engine"}
    out: dict[str, Any] = {k: v for k, v in drivers.items() if k not in computed}
    out.setdefault("model", "owner_fcf_dcf_component")
    ep_in = drivers.get("entry_price") or {}
    for k, v in ep_in.items():
        if k not in entry:
            entry[k] = v
    out.update({
        "inputs": inputs, "assumptions": assumptions, "projections": projections,
        "valuation": valuation, "entry_price": entry, "required_return_table": rrt,
        "sensitivity": sensitivity, "probability_weighted": pw,
        "engine": {"name": "dcf_engine", "version": 1,
                   "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                   "drivers": f"{drivers['ticker']}_Drivers.json"},
    })
    rec = out.get("reconciliation")
    if isinstance(rec, dict):
        rec["new_version"] = {
            "valuation_date": drivers["valuation_date"], "price_then": price,
            **{sc: valuation[sc]["intrinsic_value"] for sc in SCENARIOS},
            "weighted": pw["weighted_iv"],
        }
    return out


# ---------------------------------------------------------------------------
# Re-derivation of an existing DCF.json (the dashboard's validateEngines)
# ---------------------------------------------------------------------------
def _lenient_iv(dcf: dict[str, Any], sc: str) -> float | None:
    """The JS engine's reading of a legacy JSON: aliases honoured, base
    revenue recovered from projections, side streams from valuation[sc]."""
    a = (dcf.get("assumptions") or {}).get(sc)
    inputs = dcf.get("inputs") or {}
    if not isinstance(a, dict) or a.get("monee"):
        return None
    g = _first(a, ALIASES["growth_rates"])
    if isinstance(g, (int, float)):
        g = [float(g)] * int(a.get("horizon_years") or 5)
    if not isinstance(g, list) or not g:
        return None
    try:
        growth = [float(x) for x in g]
    except (TypeError, ValueError):
        return None
    n = len(growth)
    proj = (dcf.get("projections") or {}).get(sc) or {}
    rev = proj.get("revenue") if isinstance(proj, dict) else None
    base_rev: float | None = None
    if isinstance(rev, list) and rev and isinstance(rev[0], (int, float)):
        base_rev = rev[0] / (1 + growth[0] / 100)
    else:
        for k in ("base_revenue", "last_revenue", "revenue"):
            if isinstance(inputs.get(k), (int, float)):
                base_rev = float(inputs[k])
                break
    wacc, tg = a.get("wacc"), a.get("terminal_growth")
    if base_rev is None or not isinstance(wacc, (int, float)) or not isinstance(tg, (int, float)):
        return None
    if _first(a, ALIASES["ebitda_margin_path"]) is None:
        return None
    try:
        fcfs = _project_raw(base_rev, {**a, "growth_rates": growth})["fcf"]
    except (DriversError, TypeError, ValueError):
        return None
    cap = float(_first(a, ALIASES["terminal_cap_multiple"]) or DEFAULT_CAP)
    ps = inputs.get("projected_shares")
    shares = float(ps[n - 1]) if isinstance(ps, list) and len(ps) >= n else float(inputs.get("shares_outstanding") or 0)
    if not shares:
        return None
    v = (dcf.get("valuation") or {}).get(sc) or {}
    side = next((float(v[k]) for k in SIDE_STREAM_KEYS if isinstance(v.get(k), (int, float))), 0.0)
    tv = terminal_value(fcfs[-1], float(wacc), float(tg), cap)
    ev = _pv(fcfs, float(wacc)) + tv.value / (1 + float(wacc) / 100) ** n
    return (ev + side - float(inputs.get("net_debt") or 0)) / shares


def check(dcf: dict[str, Any], tol: float = ENGINE_TOL) -> dict[str, str]:
    """Per-scenario 'ok' | 'mismatch engine=X stored=Y' | 'no-engine', in the
    MODEL currency (the stored intrinsic_value_<cur> when the headline is in
    the quote currency)."""
    inputs = dcf.get("inputs") or {}
    cur = str(inputs.get("currency") or "").lower()
    quote = str(inputs.get("quote_currency") or "").lower()
    out: dict[str, str] = {}
    for sc in ("base", "bull", "bear"):
        v = (dcf.get("valuation") or {}).get(sc) or {}
        target = v.get("intrinsic_value")
        if cur and quote and cur != quote and isinstance(v.get(f"intrinsic_value_{cur}"), (int, float)):
            target = v[f"intrinsic_value_{cur}"]
        iv = _lenient_iv(dcf, sc)
        if iv is None or not isinstance(target, (int, float)):
            out[sc] = "no-engine"
        elif abs(iv - target) <= max(tol * abs(target), 0.005):
            out[sc] = "ok"
        else:
            out[sc] = f"mismatch engine={iv:.2f} stored={target}"
    return out
