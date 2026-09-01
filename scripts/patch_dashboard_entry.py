#!/usr/bin/env python3
"""Patch the rendered entry-price element in an already-built dashboard.

The 2026-09-01 entry-price correction changed a number the page renders outside
the embedded `dcfData` literal, in `<div class="dcf-value" id="dcfEntry">`.
`reembed_dcf.py` refreshes the data block; this refreshes that display.

DELIBERATE NON-GOAL: prose. Warning banners and KPI subtitles can carry
reasoning the corrected number invalidates -- KMD.NZ says "Entry price ($2.19)
sits ABOVE base intrinsic value ($1.87) because base-case WACC (17%) > hurdle
(15%)", which stops being true once the entry price falls to $1.54. Rewriting an
argument is a judgement call, so pages whose prose still names the superseded
value are REPORTED, never silently edited.

Usage:  patch_dashboard_entry.py --check [TICKER...]
        patch_dashboard_entry.py --apply [TICKER...]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from typing import Any

import reembed_dcf as R

ELEMENT = re.compile(r'(id="dcfEntry"[^>]*>)\s*([^<]*?)\s*(<)')
PREFIX = re.compile(r"^(-?)([^\d.\-]*)")


def _format(value: float, prefix: str, decimals: int) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}{prefix}{abs(value):,.{decimals}f}"


def patch(html: str, value: float, prefix: str,
          decimals: int | None = None) -> tuple[str, int]:
    """Replace the rendered entry price. Returns (html, elements_patched)."""
    count = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        dp = decimals
        if dp is None:
            frac = m.group(2).split(".")
            dp = len(frac[1]) if len(frac) > 1 and frac[1].isdigit() else 2
        return m.group(1) + _format(value, prefix, dp) + m.group(3)

    return ELEMENT.sub(sub, html), count


def _prefix_of(rendered: str) -> str:
    m = PREFIX.match(rendered.strip())
    return m.group(2) if m else "$"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("tickers", nargs="*")
    args = ap.parse_args(argv)

    if args.tickers:
        paths = [f"research/{t}/Reports/{t}_DCF.json" for t in args.tickers]
    else:
        paths = sorted(glob.glob("research/*/Reports/*_DCF.json"))

    patched: list[str] = []
    prose: list[tuple[str, Any, Any]] = []
    for jpath in paths:
        ticker = os.path.basename(jpath).replace("_DCF.json", "")
        hpath = f"research/{ticker}/Reports/{ticker}_Dashboard.html"
        if not (os.path.exists(jpath) and os.path.exists(hpath)):
            continue
        with open(jpath) as fh:
            dcf = json.load(fh)
        blk = ((dcf.get("entry_price") or {}).get("base")) or {}
        old, new = blk.get("entry_price_superseded"), blk.get("entry_price")
        if old is None or new is None:
            continue
        with open(hpath) as fh:
            html = fh.read()

        m = ELEMENT.search(html)
        if m:
            out, n = patch(html, float(new), _prefix_of(m.group(2)))
            if n and out != html:
                patched.append(ticker)
                if args.apply:
                    with open(hpath, "w") as fh:
                        fh.write(out)
                html = out

        # Anything still naming the old value outside the data block is prose.
        try:
            start, end = R._span(html)
            rest = html[:start] + html[end:]
        except R.NoEmbeddedDCFError:
            rest = html
        pat = re.compile(r"(?<![\d.])" + re.escape(f"{float(old):,.2f}") + r"(?![\d])")
        if pat.search(rest):
            prose.append((ticker, old, new))

    verb = "patched" if args.apply else "would patch"
    print(f"{verb} {len(patched)} dashboard element(s)")
    for ticker in patched:
        print(f"  {ticker}")
    if prose:
        print(f"\n{len(prose)} page(s) still name the superseded value in PROSE "
              "-- review by hand, the reasoning may have changed:")
        for ticker, old, new in prose:
            print(f"  {ticker:<10} {old} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
