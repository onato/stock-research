#!/usr/bin/env python3
"""Resolve a dual-currency DCF's canonical `probability_weighted.weighted_iv`.

11 of 121 corpus DCFs have no `weighted_iv`. Not one of them is missing a
valuation -- each is a dual-listed or dual-currency name whose agent wrote the
figure under a currency-suffixed key instead:

    0285.HK  weighted_iv_hkd 22.75   weighted_iv_rmb 20.91
    9626.HK  weighted_iv_usd 58.59   weighted_iv_rmb 416.00
    ASML     weighted_iv_eur 639.85  weighted_iv_usd 728.78
    CSU      weighted_intrinsic_value_usd 2068.47 / _cad 2936.08

`screen.py` reads exactly `probability_weighted.weighted_iv`, so all 11 fall
into `unranked` with a NO_IV flag and vanish from the leaderboard. They are
not marginal names -- BABA, ASML, SPOT and five HK majors are among them.

The fix is a *selection* rule, not a conversion one:

    take the variant denominated in the currency the PRICE is quoted in.

Everything here follows from picking wrong being worse than picking nothing.
9626.HK quotes HKD 138.70; read against `weighted_iv_rmb` 416.00 it shows
+200% upside and sorts to the top of the leaderboard on nothing but an FX
artifact. That is the DOW.NZ incident (+26,884% from an AUD IV over an NZD
quote, see screen.price_symbol) and the WISE.L pence-vs-USD refusal in
CLAUDE.md, one layer up the stack. A NO_IV row is a visible hole someone can
fix; a plausible wrong row is an investment decision.

DELIBERATE NON-GOALS:
  - **Never converts currency.** No FX rate is fetched or applied. If the DCF
    has no variant in the quote currency, the answer is "no" -- inventing
    2068.47 USD * 1.42 would manufacture a figure the model never produced and
    stamp today's FX onto a months-old valuation.
  - **Never writes `valuation_date`.** Same rule refresh_price.py documents:
    naming a number that already existed is not a new valuation, and this must
    not make a stale ticker look freshly researched.
  - **Never deletes the suffixed keys.** They are the evidence for the choice,
    and `weighted_iv_source` records which one was taken.
  - **Never infers the quote currency from the ticker suffix.** `.NZ` does not
    imply NZD (DOW.NZ), and a bare symbol does not imply USD (CSU quotes CAD).
    The caller passes a currency actually observed from a quote, or None.

Usage:
  canonical_iv.py --all               # report; writes nothing
  canonical_iv.py --all --apply
  canonical_iv.py --ticker 9999.HK --apply
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parents[1]

# Both spellings seen in the corpus. Order is irrelevant -- the currency
# suffix decides, never the prefix.
_PREFIXES = ("weighted_iv_", "weighted_intrinsic_value_")

CANONICAL = "weighted_iv"
SOURCE_KEY = "weighted_iv_source"

# Yahoo rate-limits; matches refresh_price.DEFAULT_DELAY.
DEFAULT_DELAY = 0.4


@dataclass(frozen=True, slots=True)
class Resolution:
    """The chosen value, and the evidence for choosing it."""

    ticker: str
    value: float | None
    source_key: str | None
    reason: str
    already_canonical: bool = False
    candidates: tuple[str, ...] = ()


def _load(repo: pathlib.Path, ticker: str) -> dict | None:
    path = repo / "research" / ticker / "Reports" / f"{ticker}_DCF.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _variants(pw: dict) -> dict[str, tuple[str, float]]:
    """Currency code (upper) -> (key, value) for every numeric variant.

    `bool` is excluded explicitly: it is a subclass of int, and a stray
    `weighted_iv_usd: true` would otherwise resolve to 1.0.
    """
    out: dict[str, tuple[str, float]] = {}
    for key, value in pw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for prefix in _PREFIXES:
            if key.startswith(prefix) and len(key) > len(prefix):
                out[key[len(prefix):].upper()] = (key, float(value))
                break
    return out


def resolve(repo: pathlib.Path | str, ticker: str, *,
            quote_currency: str | None) -> Resolution:
    """Pick the IV variant denominated like the quote, or refuse.

    `quote_currency` must come from an observed quote. GBp and GBP are
    deliberately distinct: WISE.L quotes pence against GBP filings, and
    treating them as one currency is a 100x error.
    """
    repo = pathlib.Path(repo)
    data = _load(repo, ticker)
    if data is None:
        return Resolution(ticker, None, None, "no readable DCF")

    pw = data.get("probability_weighted")
    if not isinstance(pw, dict):
        return Resolution(ticker, None, None, "no probability_weighted block")

    existing = pw.get(CANONICAL)
    if not isinstance(existing, bool) and isinstance(existing, (int, float)):
        return Resolution(ticker, float(existing), CANONICAL,
                          "already canonical", already_canonical=True)

    variants = _variants(pw)
    if not variants:
        return Resolution(ticker, None, None, "no currency-suffixed variant")

    names = tuple(sorted(v[0] for v in variants.values()))

    if not quote_currency:
        return Resolution(ticker, None, None,
                          f"unknown quote currency; {len(variants)} variants",
                          candidates=names)

    match = variants.get(quote_currency.upper())
    if match is None:
        return Resolution(
            ticker, None, None,
            f"no {quote_currency} variant (have "
            f"{', '.join(sorted(variants))})",
            candidates=names)

    # Case-sensitive guard for the GBp/GBP pair, which .upper() collapses.
    key, value = match
    if not key.endswith(quote_currency) and quote_currency in ("GBp", "GBX"):
        return Resolution(ticker, None, None,
                          f"no {quote_currency} variant (pence vs pounds)",
                          candidates=names)

    return Resolution(ticker, value, key,
                      f"matched {quote_currency} quote", candidates=names)


def apply(repo: pathlib.Path | str, ticker: str, *,
          quote_currency: str | None) -> bool:
    """Write the resolved value to `weighted_iv`. True when the file changed.

    Idempotent: a file that already carries a canonical `weighted_iv` resolves
    as `already_canonical` and is left alone.
    """
    repo = pathlib.Path(repo)
    r = resolve(repo, ticker, quote_currency=quote_currency)
    if r.value is None or r.already_canonical or r.source_key is None:
        return False

    path = repo / "research" / ticker / "Reports" / f"{ticker}_DCF.json"
    data = _load(repo, ticker)
    if data is None:
        return False
    pw = data.get("probability_weighted")
    if not isinstance(pw, dict):
        return False

    pw[CANONICAL] = r.value
    pw[SOURCE_KEY] = r.source_key
    path.write_text(_insert_keys(path.read_text(), data, r))
    return True


def _insert_keys(original: str, doc: dict, r: Resolution) -> str:
    """Add the two keys beside the variant they came from, in place.

    A full `json.dumps(indent=2)` is correct but unreviewable: it drops the
    blank lines separating blocks in 9 corpus DCFs and rewrites `2.50` as
    `2.5`, turning a two-key addition into 1804 changed lines.
    refresh_price.rewrite_values exists for the same reason, but it *replaces*
    existing scalars; this inserts new ones, so the anchor is the source
    variant's own line -- which `resolve` has already located by key.

    Falls back to re-serialisation if the edit cannot be made safely: a
    correct file with an ugly diff beats a corrupted one.
    """
    fallback = json.dumps(doc, indent=2) + "\n"
    if r.source_key is None or r.value is None:
        return fallback

    # Anchor on the source variant's line. It is unique -- a currency-suffixed
    # IV key appears once -- so no ancestor walk is needed.
    pattern = re.compile(
        r'^([ \t]*)"' + re.escape(r.source_key) + r'"\s*:\s*[^,\n]+(,?)[ \t]*$',
        re.MULTILINE)
    m = pattern.search(original)
    if m is None:
        return fallback

    indent = m.group(1)
    addition = (
        f',\n{indent}"{CANONICAL}": {_scalar(r.value)}'
        f',\n{indent}"{SOURCE_KEY}": {json.dumps(r.source_key)}'
    )
    # If the anchor line already ended with a comma, the inserted block keeps
    # it trailing so the following sibling stays valid.
    end = m.end()
    if m.group(2) == ",":
        text = original[:end - 1] + addition + "," + original[end:]
    else:
        text = original[:end] + addition + original[end:]

    try:
        json.loads(text)
    except json.JSONDecodeError:
        return fallback
    return text


def _scalar(value: float) -> str:
    """Render a number without imposing float formatting on an integer."""
    if isinstance(value, float) and value.is_integer():
        return json.dumps(int(value))
    return json.dumps(value)


def _quote_currency(root: str, ticker: str) -> str | None:
    """Observed quote currency for a ticker, or None.

    Imports the screener's own fetcher so the symbol-redirect rules
    (`info.json:price_symbol`, the BGI.NZ/DOW.NZ cases) apply identically.
    """
    skill = REPO / ".claude" / "skills" / "screen-investments"
    if str(skill) not in sys.path:
        sys.path.insert(0, str(skill))
    try:
        import screen
    except ImportError:
        return None
    _, currency = screen.fetch_live_price(screen.price_symbol(root, ticker))
    return currency


def _tickers(repo: pathlib.Path) -> list[str]:
    root = repo / "research"
    if not root.is_dir():
        return []
    return sorted(p.parent.parent.name
                  for p in root.glob("*/Reports/*_DCF.json"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ticker", default="")
    p.add_argument("--all", action="store_true")
    p.add_argument("--apply", action="store_true",
                   help="write the canonical key (default: report only)")
    p.add_argument("--root", default="research")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = p.parse_args()

    names = [args.ticker] if args.ticker else _tickers(REPO)
    if not names:
        print("  nothing to do")
        return 0

    fixed = refused = skipped = 0
    for ticker in names:
        pre = resolve(REPO, ticker, quote_currency=None)
        if pre.already_canonical:
            skipped += 1
            continue
        if pre.reason == "no currency-suffixed variant":
            skipped += 1
            continue

        ccy = _quote_currency(args.root, ticker)
        time.sleep(args.delay)
        r = resolve(REPO, ticker, quote_currency=ccy)

        if r.value is None:
            refused += 1
            print(f"  REFUSED  {ticker:10s} {r.reason}")
            continue

        if args.apply and apply(REPO, ticker, quote_currency=ccy):
            fixed += 1
            print(f"  fixed    {ticker:10s} {CANONICAL} = {r.value} "
                  f"(from {r.source_key})")
        else:
            fixed += 1
            print(f"  would    {ticker:10s} {CANONICAL} = {r.value} "
                  f"(from {r.source_key})")

    verb = "fixed" if args.apply else "resolvable"
    print(f"\n  {verb}: {fixed}   refused: {refused}   "
          f"already canonical: {skipped}")
    if not args.apply and fixed:
        print("  (report only -- pass --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
