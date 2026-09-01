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
PV_INTERIM_KEYS = ("pv_interim_fcf_at_hurdle", "pv_interim_at_hurdle")

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

        # A share count that grows across the projection (DUOL 49.0m ->
        # 60.64m, CCC.NZ, ENS.NZ, GNE.NZ) means the terminal value is divided
        # by a different denominator than today's. Recomputing on a flat count
        # would silently restate the model, so refuse.
        proj_shares = proj.get("shares")
        terminal_shares = F.num(blk.get("terminal_year_shares"))
        if isinstance(proj_shares, list) and proj_shares:
            last = F.num(proj_shares[-1])
            if last and abs(last - shares) / shares > 0.01:
                raise NotRecomputableError(
                    f"{scen}: share count grows {shares:g} -> {last:g}; "
                    "terminal value uses a different divisor")
        if terminal_shares and abs(terminal_shares - shares) / shares > 0.01:
            raise NotRecomputableError(
                f"{scen}: terminal_year_shares={terminal_shares:g} != "
                f"shares_outstanding={shares:g}")

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
        new = _entry(fcf, hurdle, tg, cap, net_debt, shares)
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
        blk["pv_interim_fcf_at_hurdle"] = round(pv_calc, 2)
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
