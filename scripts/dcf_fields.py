#!/usr/bin/env python3
"""Field extraction for the DCF.json files, shared by ledger.py and run_evals.py.

The 67 committed DCF files agree on structure (scenario dict, probability
weights, entry price block) but not on spelling: scenario IVs appear under 15
key names (`intrinsic_value`, `intrinsic_value_hkd`, `intrinsic_value_per_adr_usd`,
...), the scenario block is `valuation` in 64 files and `scenarios` in 3, and
entry prices are sometimes scalars, sometimes dicts keyed per currency.

Everything here extracts RAW key->value dicts rather than picking a winner:
the ledger must not lose information (currency resolution happens at scoring
time, months later), and the checker matches recomputed values against every
candidate.
"""

import hashlib
import json
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]

SCENARIOS = ("bear", "base", "bull")

# Trailing tokens that mark a currency-qualified key. `gbp_pence` before
# `gbp` so the longer suffix wins.
CURRENCIES = (
    "gbp_pence", "pence", "usd", "hkd", "rmb", "cny", "nzd",
    "aud", "eur", "cad", "gbp",
)


def num(v):
    """Coerce to float, else None. Accepts '1,234' and '25%' strings."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").replace("%", "").strip().rstrip(".")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def currency_suffix(key):
    """'intrinsic_value_per_share_hkd' -> 'hkd'; 'intrinsic_value' -> ''."""
    k = key.lower()
    for cur in CURRENCIES:
        if k.endswith("_" + cur):
            return cur
    return ""


def load_dcf(ticker):
    """Parsed DCF.json for a ticker, or None."""
    path = REPO / "research" / ticker / "Reports" / f"{ticker}_DCF.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def scenario_block(dcf):
    """The base/bear/bull dict: `valuation` in most files, `scenarios` in a few."""
    for key in ("valuation", "scenarios"):
        block = dcf.get(key)
        if isinstance(block, dict) and any(s in block for s in SCENARIOS):
            return block
    return {}


def scenario_ivs(dcf):
    """{scenario: {key: value}} for every intrinsic-value-ish numeric key."""
    out = {}
    for s, sc in scenario_block(dcf).items():
        if s not in SCENARIOS or not isinstance(sc, dict):
            continue
        ivs = {}
        for k, v in sc.items():
            if "intrinsic" not in k.lower():
                continue
            n = num(v)
            if n is not None:
                ivs[k] = n
        if ivs:
            out[s] = ivs
    return out


def weights(dcf):
    """{scenario: weight} normalized to sum to ~1, or {}."""
    pw = dcf.get("probability_weighted")
    w = pw.get("weights") if isinstance(pw, dict) else None
    if not isinstance(w, dict):
        return {}
    out = {s: num(v) for s, v in w.items() if s in SCENARIOS and num(v) is not None}
    total = sum(out.values())
    if 99 <= total <= 101:  # stored as percentages
        out = {s: v / 100 for s, v in out.items()}
    return out


def weighted_ivs(dcf):
    """{key: value} for every weighted-IV numeric key in probability_weighted."""
    pw = dcf.get("probability_weighted")
    if not isinstance(pw, dict):
        return {}
    out = {}
    for k, v in pw.items():
        kl = k.lower()
        if kl.startswith("weighted_iv") or kl.startswith("weighted_intrinsic"):
            n = num(v)
            if n is not None:
                out[k] = n
    return out


def weighted_upsides(dcf):
    """{key: value} for weighted/aggregate upside keys in probability_weighted."""
    pw = dcf.get("probability_weighted")
    if not isinstance(pw, dict):
        return {}
    out = {}
    for k, v in pw.items():
        if "upside" in k.lower():
            n = num(v)
            if n is not None:
                out[k] = n
    return out


def entry_prices(dcf):
    """{scenario_or_key: {key: value}} of numeric entry-price fields.

    Handles both shapes: `entry_price.base` as a dict of fields, as a bare
    number, or flat keys like PNG.V's `base_case_entry`.
    """
    ep = dcf.get("entry_price")
    if not isinstance(ep, dict):
        return {}
    out = {}
    for k, v in ep.items():
        if isinstance(v, dict):
            fields = {fk: num(fv) for fk, fv in v.items()
                      if "entry" in fk.lower() and num(fv) is not None}
            if fields:
                out[k] = fields
        else:
            n = num(v)
            if n is not None and "entry" in k.lower():
                out.setdefault("_flat", {})[k] = n
    return out


def hurdle_rate(dcf):
    ep = dcf.get("entry_price")
    if isinstance(ep, dict):
        for k in ("hurdle_rate", "target_return", "target_cagr", "target_irr"):
            n = num(ep.get(k))
            if n is not None:
                return n
    return None


def agents_sha():
    """Content hash of the agent prompts that produced this run.

    Hashes the working-tree files rather than a git object so uncommitted
    prompt edits still get a distinct identity.
    """
    h = hashlib.sha256()
    for p in sorted((REPO / ".claude" / "agents").glob("*.md")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def git_head():
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or None
    except OSError:
        return None
