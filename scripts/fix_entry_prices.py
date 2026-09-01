#!/usr/bin/env python3
"""Recompute hurdle entry prices whose terminal value was built at WACC.

Until 2026-09-01 `dcf-methods/references/owner-fcf.md` told the agent to hold
the WACC-built, WACC-capped terminal value inside the 15%-IRR entry price and
vary only the discounting. That overstated entry prices on 42 committed DCFs by
a median ~27% (see run_evals.check_entry_price_hurdle for why it is wrong).

Nothing about the underlying valuation is affected: the flows, the WACC, the
scenarios and every intrinsic value stand. Only the entry-price arithmetic was
wrong, and every input it needs is already in the JSON -- so this repairs the
files by recomputation and never re-runs a model.

DELIBERATE NON-GOALS. This script does not touch intrinsic value, weights,
projections or assumptions, and it refuses rather than guesses when a file
means something it does not model:

  * non-FCF models (SUM.NZ discounts Underlying Profit, RYM.NZ an NTA path)
    have no `fcf` series to recompute from;
  * files whose own stated `pv_interim_*_at_hurdle` disagrees with discounting
    their `fcf` at the hurdle are using a series we cannot see, so the terminal
    leg cannot be safely isolated either.

Both raise NotRecomputableError and are reported for human review.

Usage:  fix_entry_prices.py --check           # report, write nothing
        fix_entry_prices.py --apply [TICKER]  # rewrite the JSON in place
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Any

import dcf_fields as F

SCENARIOS = ("bear", "base", "bull")
FCF_KEYS = ("fcf", "owner_fcf", "free_cash_flow")
PV_INTERIM_KEYS = ("pv_interim_fcf_at_hurdle", "pv_interim_at_hurdle",
                   "pv_interim_distributions_at_hurdle")

# Non-operating value the equity bridge adds back on top of -net_debt.
BRIDGE_ADDBACK_KEYS = ("equity_investments", "associates", "non_operating_assets")

# Spellings for "the share count the entry price is divided by at exit".
TERMINAL_SHARE_KEYS = ("terminal_year_shares", "exit_shares",
                       "terminal_shares", "shares_at_exit")

# A label or note sitting beside the `fcf` series means those rows are NOT owner
# free cash flow: SUM.NZ stores Underlying Profit under `fcf` (`fcf_label`), and
# RYM.NZ stores dividends paid (`fcf_note`). Recomputing a Gordon terminal value
# off either is meaningless -- SUM.NZ's entry price moved +15,282% in a dry run.
RELABEL_MARKERS = ("fcf_label", "fcf_note", "owner_fcf_label")

# Provenance stamped onto every repaired scenario so a later reader can tell a
# recomputed number from an agent-authored one.
NOTE = ("Terminal value rebuilt at the hurdle rate 2026-09-01: "
        "min(Gordon@hurdle, cap x FCF_N). The superseded value held the "
        "WACC-built terminal value and varied only the discounting.")


class NotRecomputableError(Exception):
    """The file does not carry an FCF series this script can reproduce."""


def _fcf(proj: dict[str, Any]) -> list[float] | None:
    for key in FCF_KEYS:
        v = proj.get(key)
        if isinstance(v, list) and v and all(F.num(x) is not None for x in v):
            return [float(F.num(x) or 0.0) for x in v]
    return None


def _entry(fcf: list[float], hurdle: float, tg: float,
           cap: float | None, net_debt: float, shares: float) -> float:
    """PV at the hurdle of the flows plus a terminal value built AT THAT RATE."""
    pv = sum(f / (1 + hurdle) ** (i + 1) for i, f in enumerate(fcf))
    gordon = fcf[-1] * (1 + tg) / (hurdle - tg)
    tv = min(gordon, fcf[-1] * cap) if cap else gordon
    return (pv + tv / (1 + hurdle) ** len(fcf) - net_debt) / shares


def fix(dcf: dict[str, Any]) -> dict[str, Any]:
    """Rewrite `dcf['entry_price']` in place. Idempotent.

    Raises NotRecomputableError if the file is not an FCF model, or if its own
    stated interim PV disagrees with our reconstruction.
    """
    ep = dcf.get("entry_price")
    if not isinstance(ep, dict):
        raise NotRecomputableError("no entry_price block")
    hurdle = F.num(ep.get("hurdle_rate"))
    if hurdle is None:
        raise NotRecomputableError("no hurdle_rate")
    if hurdle > 1:
        hurdle /= 100.0

    inputs = dcf.get("inputs") or {}
    shares = F.num(inputs.get("shares_outstanding"))
    net_debt = F.num(inputs.get("net_debt"))
    if not shares or net_debt is None:
        raise NotRecomputableError("no shares_outstanding / net_debt")

    for scen in SCENARIOS:
        blk = ep.get(scen)
        if not isinstance(blk, dict) or F.num(blk.get("entry_price")) is None:
            continue
        proj = (dcf.get("projections") or {}).get(scen) or {}
        asm = (dcf.get("assumptions") or {}).get(scen) or {}
        for marker in RELABEL_MARKERS:
            if proj.get(marker):
                raise NotRecomputableError(
                    f"{scen}: {marker}={proj[marker]!r} -- the fcf rows are not "
                    "owner free cash flow")
        fcf = _fcf(proj)
        tg = F.num(asm.get("terminal_growth"))
        if fcf is None or tg is None:
            raise NotRecomputableError(f"{scen}: no fcf series / terminal growth")
        tg /= 100.0
        if hurdle <= tg:
            raise NotRecomputableError(f"{scen}: hurdle <= terminal growth")

        # A share count that grows across the projection means the entry
        # price is divided by a different denominator than today's count
        # (CCC.NZ 66.4m -> 93.6m, ENS.NZ, GNE.NZ, DUOL 49.0m -> 60.64m).
        # Where the file states which count it used we honour it; where it
        # only implies one we refuse, because guessing the divisor silently
        # restates the model.
        divisor = shares
        terminal_shares = next(
            (F.num(blk[k]) for k in TERMINAL_SHARE_KEYS
             if F.num(blk.get(k)) is not None), None)
        proj_shares = proj.get("shares") or proj.get("projected_shares")
        implied = None
        if isinstance(proj_shares, list) and proj_shares:
            implied = F.num(proj_shares[-1])
        if terminal_shares:
            divisor = terminal_shares
        elif implied and abs(implied - shares) / shares > 0.01:
            # The file may still PROVE its divisor: entry_equity_value /
            # entry_price is the count it actually divided by (ENS.NZ states
            # both but names neither). Trust that only when it corroborates
            # the projected share path -- then it is the file's own
            # arithmetic, not a guess.
            eq_val = F.num(blk.get("entry_equity_value"))
            old_entry = F.num(blk.get("entry_price"))
            proven = (eq_val / old_entry
                      if eq_val is not None and old_entry else None)
            if proven and abs(proven - implied) / implied <= 0.01:
                divisor = implied
            else:
                raise NotRecomputableError(
                    f"{scen}: share count grows {shares:g} -> {implied:g} but "
                    f"no {'/'.join(TERMINAL_SHARE_KEYS[:2])} states which "
                    "divisor was used")

        # Non-operating assets the bridge adds back beyond net debt (ENS.NZ
        # carries `equity_investments` of NZ$1.199m). Dropping them would
        # understate the entry price.
        bridge_adds = sum(
            F.num(v) or 0.0 for k, v in blk.items()
            if k in BRIDGE_ADDBACK_KEYS)

        horizon = F.num(blk.get("years_to_terminal"))
        if horizon and 0 < int(horizon) <= len(fcf):
            fcf = fcf[:int(horizon)]

        # Guard: the file's own interim PV must match discounting this series
        # at the hurdle. If it does not, the entry price was built off flows we
        # cannot see and the terminal leg cannot be isolated.
        pv_calc = sum(f / (1 + hurdle) ** (i + 1) for i, f in enumerate(fcf))
        for key, stated_v in blk.items():
            if not (key.startswith("pv_interim") or key in PV_INTERIM_KEYS):
                continue
            stated = F.num(stated_v)
            if stated is None:
                continue
            if abs(stated - pv_calc) > max(0.02 * abs(pv_calc), 1e-6):
                raise NotRecomputableError(
                    f"{scen}: stated {key}={stated:.1f} != {pv_calc:.1f} "
                    "from the fcf series (different series or currency)")

        cap = F.num(asm.get("terminal_cap_multiple"))
        scen_nd = F.num(blk.get("net_debt"))
        nd = scen_nd if scen_nd is not None else net_debt
        new = _entry(fcf, hurdle, tg, cap, nd - bridge_adds, divisor)
        old = F.num(blk["entry_price"])

        # Record the superseded value once, so re-running never overwrites the
        # original agent-authored number with an already-corrected one.
        if "entry_price_superseded" not in blk and old is not None \
                and abs(old - new) > max(0.005 * abs(new), 0.005):
            blk["entry_price_superseded"] = old
            blk["entry_price_correction_note"] = NOTE

        blk["entry_price"] = round(new, 4 if abs(new) < 1 else 2)
        gordon = fcf[-1] * (1 + tg) / (hurdle - tg)
        tv = min(gordon, fcf[-1] * cap) if cap else gordon
        pv_key = next((k for k in PV_INTERIM_KEYS if k in blk),
                      "pv_interim_fcf_at_hurdle")
        blk[pv_key] = round(pv_calc, 2)
        blk["pv_terminal_at_hurdle"] = round(tv / (1 + hurdle) ** len(fcf), 2)
        if fcf[-1]:
            blk["terminal_multiple_at_hurdle"] = round(tv / fcf[-1], 2)
        else:
            blk.pop("terminal_multiple_at_hurdle", None)
        price = F.num(dcf.get("current_price"))
        if price:
            blk["entry_discount_from_current"] = round(
                (blk["entry_price"] / price - 1) * 100, 1)

    weights = ((dcf.get("probability_weighted") or {}).get("weights")) or {}
    if len(weights) == 3:
        tot = 0.0
        for scen, w in weights.items():
            v = F.num((ep.get(scen) or {}).get("entry_price"))
            wn = F.num(w)
            if v is None or wn is None:
                break
            tot += wn * v
        else:
            ep["weighted_entry_price"] = round(tot, 4 if abs(tot) < 1 else 2)

    rrt = dcf.get("required_return_table")
    if isinstance(rrt, dict) and isinstance(rrt.get("returns"), list):
        proj = (dcf.get("projections") or {}).get("base") or {}
        asm = (dcf.get("assumptions") or {}).get("base") or {}
        fcf = _fcf(proj)
        tg = F.num(asm.get("terminal_growth"))
        if fcf is not None and tg is not None:
            tg /= 100.0
            cap = F.num(asm.get("terminal_cap_multiple"))
            rows: list[float | None] = []
            for r in rrt["returns"]:
                rn = F.num(r)
                if rn is None:
                    rows.append(None)
                    continue
                rn = rn / 100.0 if rn > 1 else rn
                rows.append(round(_entry(fcf, rn, tg, cap, net_debt, shares), 2)
                            if rn > tg else None)
            if all(x is not None for x in rows):
                rrt["value_per_share"] = rows
                rrt["note"] = (
                    "Each row rebuilds the terminal value at that row's required "
                    "return (min(Gordon@r, cap x FCF_N)), so the flows and the exit "
                    "are charged the same rate. Corrected 2026-09-01.")
    return ep


def fix_capitalized(dcf: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a CAPITALIZED-EARNINGS entry price at the hurdle rate.

    SUM.NZ capitalizes Underlying Profit at a cost of equity with an exit-P/E
    cap. Its terminal value was built at the 11% CoE and then discounted at
    15% -- the same defect as the FCF case, because the rule is about the RATE,
    not the flow. But the flow is not owner FCF, so `fix()` refuses it and this
    explicit entry point handles it instead.

    Two things stay true for this engine and are why it cannot share `fix()`:
    the terminal cap is an exit P/E on the flow (not an FCF multiple), and net
    debt is NEVER subtracted -- Underlying Profit is already post-finance-cost,
    so deducting debt would double-count it.
    """
    ep = dcf.get("entry_price")
    if not isinstance(ep, dict):
        raise NotRecomputableError("no entry_price block")
    hurdle = F.num(ep.get("hurdle_rate"))
    if hurdle is None:
        raise NotRecomputableError("no hurdle_rate")
    if hurdle > 1:
        hurdle /= 100.0
    shares = F.num((dcf.get("inputs") or {}).get("shares_outstanding"))
    if not shares:
        raise NotRecomputableError("no shares_outstanding")

    for scen in SCENARIOS:
        blk = ep.get(scen)
        if not isinstance(blk, dict) or F.num(blk.get("entry_price")) is None:
            continue
        proj = (dcf.get("projections") or {}).get(scen) or {}
        asm = (dcf.get("assumptions") or {}).get(scen) or {}
        flow = _fcf(proj)
        tg = F.num(asm.get("terminal_growth"))
        pe = F.num(asm.get("exit_pe_cap")) or F.num(asm.get("exit_pe"))
        if flow is None or tg is None or pe is None:
            raise NotRecomputableError(f"{scen}: no flow / terminal growth / exit P/E")
        tg /= 100.0
        if hurdle <= tg:
            raise NotRecomputableError(f"{scen}: hurdle <= terminal growth")

        horizon = F.num(blk.get("years_to_terminal"))
        if horizon and 0 < int(horizon) <= len(flow):
            flow = flow[:int(horizon)]

        pv = sum(f / (1 + hurdle) ** (i + 1) for i, f in enumerate(flow))
        for key, stated_v in blk.items():
            if not key.startswith("pv_interim"):
                continue
            stated = F.num(stated_v)
            if stated is not None and abs(stated - pv) > max(0.02 * abs(pv), 1e-6):
                raise NotRecomputableError(
                    f"{scen}: stated {key}={stated:.1f} != {pv:.1f}")

        gordon = flow[-1] * (1 + tg) / (hurdle - tg)
        tv = min(gordon, flow[-1] * pe)
        new = (pv + tv / (1 + hurdle) ** len(flow)) / shares
        old = F.num(blk["entry_price"])

        if "entry_price_superseded" not in blk and old is not None \
                and abs(old - new) > max(0.005 * abs(new), 0.005):
            blk["entry_price_superseded"] = old
            blk["entry_price_correction_note"] = NOTE
        blk["entry_price"] = round(new, 2)
        blk["pv_terminal_at_hurdle"] = round(tv / (1 + hurdle) ** len(flow), 1)
        blk["terminal_value_per_share"] = round(tv / shares, 2)
        blk["terminal_multiple_at_hurdle"] = round(tv / flow[-1], 2)
        price = F.num(dcf.get("current_price"))
        if price:
            blk["entry_discount_from_current"] = round((new / price - 1) * 100, 1)

    weights = ((dcf.get("probability_weighted") or {}).get("weights")) or {}
    if len(weights) == 3:
        tot = 0.0
        for scen, w in weights.items():
            v = F.num((ep.get(scen) or {}).get("entry_price"))
            wn = F.num(w)
            if v is None or wn is None:
                break
            tot += wn * v
        else:
            ep["weighted_entry_price"] = round(tot, 2)
    return ep


def _paths(tickers: list[str]) -> list[str]:
    if tickers:
        return [f"research/{t}/Reports/{t}_DCF.json" for t in tickers]
    return sorted(glob.glob("research/*/Reports/*_DCF.json"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="report, write nothing")
    g.add_argument("--apply", action="store_true", help="rewrite JSON in place")
    ap.add_argument("tickers", nargs="*")
    args = ap.parse_args(argv)

    changed: list[tuple[str, float, float]] = []
    refused: list[tuple[str, str]] = []
    for path in _paths(args.tickers):
        if not os.path.exists(path):
            continue
        ticker = os.path.basename(path).replace("_DCF.json", "")
        with open(path) as fh:
            raw = fh.read()
        dcf = json.loads(raw)
        before = F.num(((dcf.get("entry_price") or {}).get("base") or {})
                       .get("entry_price"))
        try:
            fix(dcf)
        except NotRecomputableError as exc:
            refused.append((ticker, str(exc)))
            continue
        after = F.num(((dcf.get("entry_price") or {}).get("base") or {})
                      .get("entry_price"))
        if before is None or after is None or abs(before - after) <= max(
                0.005 * abs(after), 0.005):
            continue
        changed.append((ticker, before, after))
        if args.apply:
            with open(path, "w") as fh:
                json.dump(dcf, fh, indent=2, ensure_ascii=False)
                fh.write("\n")

    verb = "rewrote" if args.apply else "would rewrite"
    print(f"{verb} {len(changed)} DCF(s)")
    for ticker, before, after in changed:
        pct = (before - after) / abs(after) * 100 if after else float("nan")
        print(f"  {ticker:<10} {before:>12,.2f} -> {after:>12,.2f}  ({pct:+.0f}%)")
    if refused:
        print(f"\nrefused {len(refused)} (need human review, not recomputable):")
        for ticker, why in refused:
            print(f"  {ticker:<10} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
