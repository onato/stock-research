#!/usr/bin/env python3
"""Record each DCF's OBSERVED quote currency in `inputs.quote_currency`.

The field became required on 2026-09-01 (see run_evals.check_currency_contract);
146 committed files predate it. It exists because a ticker's exchange suffix
does not imply either its reporting currency or, for the awkward cases, the
currency it trades in: SMI.NZ and MKR.NZ file AUD on the NZX, ANZ.NZ and EBO.NZ
file AUD, ARB.NZ files USD, WISE.L files USD and quotes GBp, NetEase files RMB
against an HKD quote, and 3 of 48 bare US symbols (ASML, ADYEY, SPOT) file EUR.

So the value is always OBSERVED from a live quote via the screener's own fetcher
-- which applies the `info.json:price_symbol` redirects -- and never inferred.
An unobservable quote is skipped, not guessed.

DELIBERATE NON-GOALS: this never touches `inputs.currency` (that is the FILING
currency and must come from the statements), never converts a value, and never
overwrites a stated quote currency that disagrees with the observed one -- that
disagreement is a finding for a human.

Usage:  backfill_quote_currency.py --check [TICKER...]
        backfill_quote_currency.py --apply [TICKER...]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]

FX_PLACEHOLDER = (
    "TODO: filing currency differs from the quote currency. Record the rate "
    "used, its date, its source, and a parity check (a dual listing if one "
    "exists), and confirm the canonical intrinsic_value / weighted_iv are "
    "stated in the QUOTE currency so they are comparable to current_price."
)


class NotObservedError(Exception):
    """No usable observed quote currency."""


class ConflictError(Exception):
    """A stated quote currency disagrees with the observed one."""


def _valid(code: Any) -> str | None:
    """The code if it is a bare ISO-4217 currency (GBp allowed), else None."""
    if not isinstance(code, str):
        return None
    c = code.strip()
    if len(c) != 3 or not c.isalpha():
        return None
    return c if c == "GBp" else (c.upper() if c.isupper() else None)


def apply(dcf: dict[str, Any], observed: Any) -> bool:
    """Write `inputs.quote_currency`. True if the file changed.

    Raises NotObservedError for an unusable observation, ConflictError when the
    file already states a different quote currency.
    """
    code = _valid(observed)
    if code is None:
        raise NotObservedError(f"not an ISO currency: {observed!r}")
    inputs = dcf.setdefault("inputs", {})
    existing = inputs.get("quote_currency")
    if existing is not None:
        if _valid(existing) == code:
            return False
        raise ConflictError(
            f"file says quote_currency={existing!r}, observed {code!r}")

    inputs["quote_currency"] = code
    filing = _valid(inputs.get("currency"))
    if filing and filing != code and not str(inputs.get("fx_note") or "").strip():
        inputs["fx_note"] = FX_PLACEHOLDER
    return True


def observe(ticker: str) -> str | None:
    """Live quote currency for a ticker, via the screener's fetcher."""
    skill = REPO / ".claude" / "skills" / "screen-investments"
    if str(skill) not in sys.path:
        sys.path.insert(0, str(skill))
    try:
        import screen
    except ImportError:
        return None
    try:
        _, currency = screen.fetch_live_price(screen.price_symbol("research", ticker))
    except Exception:
        return None
    return currency


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

    wrote, skipped, conflicts = [], [], []
    for path in paths:
        if not os.path.exists(path):
            continue
        ticker = os.path.basename(path).replace("_DCF.json", "")
        with open(path) as fh:
            dcf = json.load(fh)
        if dcf.get("inputs", {}).get("quote_currency") is not None:
            continue
        observed = observe(ticker)
        try:
            changed = apply(dcf, observed)
        except NotObservedError as exc:
            skipped.append((ticker, str(exc)))
            continue
        except ConflictError as exc:
            conflicts.append((ticker, str(exc)))
            continue
        if not changed:
            continue
        filing = dcf["inputs"].get("currency")
        wrote.append((ticker, filing, dcf["inputs"]["quote_currency"]))
        if args.apply:
            with open(path, "w") as fh:
                json.dump(dcf, fh, indent=2, ensure_ascii=False)
                fh.write("\n")

    verb = "recorded" if args.apply else "would record"
    print(f"{verb} quote_currency on {len(wrote)} DCF(s)")
    mismatched = [w for w in wrote if w[1] and _valid(w[1]) != w[2]]
    for ticker, filing, quote in mismatched:
        print(f"  {ticker:<10} files {filing} but quotes {quote}  <-- needs an fx_note")
    if skipped:
        print(f"\nskipped {len(skipped)} (no observable quote):")
        for ticker, why in skipped[:15]:
            print(f"  {ticker:<10} {why}")
    if conflicts:
        print(f"\n{len(conflicts)} CONFLICT(s) -- review by hand:")
        for ticker, why in conflicts:
            print(f"  {ticker:<10} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
