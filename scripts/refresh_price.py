#!/usr/bin/env python3
"""Update a DCF's price-derived numbers without running a model.

`weighted_iv = sum(weight_s * IV_s)` and
`entry_price = terminal_value_per_share / (1 + hurdle) ** years` are both
independent of the current price -- recomputing DCBO's weighted IV from its
scenario IVs reproduces the stored 33.0 exactly. Only `upside` and
`entry_discount_from_current` move when the market does.

So a stale price does not invalidate a valuation. It invalidates two derived
numbers, and the prose written around them. The numbers are arithmetic and
are fixed here for free; the prose is not, and is only flagged.

DCBO, measured: price 17.52 -> 23.06 (+31.6%) moves the base case from +90%
to +44% upside, flips the bear case from +6% to -19%, and turns a base entry
price that was 15.6% ABOVE the market into one 12.1% BELOW it. That is a real
signal, and before this script the only way to surface it was a ~$6
re-research that would have re-derived identical financials.

DELIBERATE NON-GOALS:
  - Never writes `valuation_date`. It is the staleness key read by
    filter_tickers, select_ticker and screen.py; a price tick is not a new
    valuation and must not make a ticker look freshly researched.
  - Never writes prose. 101 of 113 corpus DCFs quote the price inside
    sentences whose claims change with it, and substituting the number would
    leave the claim wrong ("entry price is 15.6% above current price" is not
    repaired by swapping the figure). Those paths are listed in the
    `price_refresh` block for a scoped agent to rewrite.
  - Never invents a field. 22 of 113 files have no `valuation.{s}.upside`;
    absent stays absent.
  - Never appends to the ledger. Its row key includes `current_price`, so a
    price-only refresh would mint a row for a prediction nobody made.

Usage:
  refresh_price.py --ticker DCBO           # check only (default)
  refresh_price.py --ticker DCBO --apply
  refresh_price.py --all --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field

from prune_queue import quote

REPO = pathlib.Path(__file__).resolve().parents[1]

SCENARIOS = ("bear", "base", "bull")

# Yahoo rate-limits; prune_queue uses the same spacing.
DEFAULT_DELAY = 0.4

# Currencies that are the same money in different units. A quote in GBp
# against filings in GBP is a 100x error, so these must still not be mixed --
# the pair is listed to document that the check is deliberate, not naive.
_CURRENCY_ALIASES = {"GBP": "GBP", "GBX": "GBp", "GBP PENCE": "GBp"}


@dataclass
class Result:
    """What one refresh did, or refused to do."""

    ticker: str
    ok: bool = False
    reason: str = ""
    would_change: bool = False
    previous_price: float | None = None
    current_price: float | None = None
    drift_pct: float | None = None
    fields_updated: list[str] = field(default_factory=list)
    prose_paths: list[str] = field(default_factory=list)
    dashboard_updated: bool = False


def _dcf_path(repo: pathlib.Path, ticker: str) -> pathlib.Path:
    return repo / "research" / ticker / "Reports" / f"{ticker}_DCF.json"


def _canon_currency(code: str | None) -> str:
    text = (code or "").strip()
    return _CURRENCY_ALIASES.get(text.upper(), text)


def price_spellings(price: float) -> set[str]:
    """Every way a price is plausibly written into prose.

    Corpus files carry `$17.52`, `17.5`, `$1,234.50` and bare `17.52`, so a
    single format would miss most of them.
    """
    out = {f"{price:.2f}", f"{price:g}", f"{price:,.2f}", str(price)}
    return {s for s in out if s}


def find_prose_paths(node: object, needles: set[str],
                     path: str = "") -> list[str]:
    """Dotted paths of every string containing one of `needles`.

    These are the sentences that will contradict the new numbers. They are
    reported, never edited -- see the module docstring.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "price_refresh":      # our own bookkeeping
                continue
            found += find_prose_paths(value, needles,
                                      f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += find_prose_paths(value, needles, f"{path}[{i}]")
    elif isinstance(node, str) and any(n in node for n in needles):
        found.append(path)
    return found


def _round(value: float) -> float:
    return round(value, 1)


_IV_SERIES_RE = re.compile(r"^intrinsic_value[_a-z]*$")


def denomination_conflict(doc: dict, price: float) -> str | None:
    """Reason to refuse when `intrinsic_value` is not in the price's units.

    23 corpus DCFs carry parallel per-currency IV series and 15 of them leave
    the top-level `currency` null, so a currency-code comparison cannot catch
    them. WISE.L is the shape that matters: the London quote is 885.6 pence
    while `intrinsic_value` is 13.5 USD, and its stored upsides track the
    `intrinsic_value_gbp_pence` series (1001.1 -> 13.0%). Recomputing from
    the plain field would write -98.5% over three correct numbers.

    The existing stored upside is the evidence: it was computed against the
    right series by whoever built the model, so if it matches a *sibling*
    series better than it matches `intrinsic_value`, the plain field is not
    the one this file's upsides are denominated in. Only a clear
    contradiction refuses -- a file with no siblings, or whose plain field
    already agrees, is left alone.
    """
    valuation = doc.get("valuation")
    if not isinstance(valuation, dict):
        return None
    stored_price = doc.get("current_price")
    if not isinstance(stored_price, (int, float)) or stored_price <= 0:
        return None

    for name in SCENARIOS:
        block = valuation.get(name)
        if not isinstance(block, dict):
            continue
        upside = block.get("upside")
        plain = block.get("intrinsic_value")
        if not isinstance(upside, (int, float)):
            continue
        siblings = {k: v for k, v in block.items()
                    if _IV_SERIES_RE.match(k) and k != "intrinsic_value"
                    and isinstance(v, (int, float)) and v}
        if not siblings:
            continue
        # How far each candidate is from reproducing the stored upside.
        # Bound as defaults so the closure cannot capture the loop variable
        # and score a later scenario's numbers against this one's upside.
        want: float = float(upside)
        base_price: float = float(stored_price)

        def gap(value: float, want: float = want,
                base_price: float = base_price) -> float:
            return abs((value / base_price - 1.0) * 100.0 - want)

        best = min(siblings, key=lambda k: gap(siblings[k]))
        plain_gap = gap(plain) if isinstance(plain, (int, float)) and plain \
            else float("inf")
        if gap(siblings[best]) < 1.0 <= plain_gap:
            return (f"denomination mismatch: {name} upside {upside} tracks "
                    f"{best}, not intrinsic_value -- needs a "
                    f"currency-aware refresh")
    return None


def apply_price(doc: dict, price: float) -> list[str]:
    """Recompute every price-derived number in place. Returns paths changed.

    Only fields that already exist are touched.
    """
    changed: list[str] = []

    if doc.get("current_price") != price:
        doc["current_price"] = price
        changed.append("current_price")

    valuation = doc.get("valuation")
    if isinstance(valuation, dict):
        for name in SCENARIOS:
            block = valuation.get(name)
            if not isinstance(block, dict) or "upside" not in block:
                continue
            iv = block.get("intrinsic_value")
            if not isinstance(iv, (int, float)):
                continue
            want = _round((iv / price - 1.0) * 100.0)
            if block["upside"] != want:
                block["upside"] = want
                changed.append(f"valuation.{name}.upside")

    entry = doc.get("entry_price")
    if isinstance(entry, dict):
        for name in SCENARIOS:
            block = entry.get(name)
            if (not isinstance(block, dict)
                    or "entry_discount_from_current" not in block):
                continue
            ep = block.get("entry_price")
            if not isinstance(ep, (int, float)):
                continue
            want = _round(((price - ep) / price) * -100.0)
            if block["entry_discount_from_current"] != want:
                block["entry_discount_from_current"] = want
                changed.append(f"entry_price.{name}.entry_discount_from_current")

    pw = doc.get("probability_weighted")
    if isinstance(pw, dict) and "weighted_upside" in pw:
        wiv = pw.get("weighted_iv")
        if isinstance(wiv, (int, float)):
            want = _round((wiv / price - 1.0) * 100.0)
            if pw["weighted_upside"] != want:
                pw["weighted_upside"] = want
                changed.append("probability_weighted.weighted_upside")

    thesis = doc.get("investment_thesis")
    if isinstance(thesis, dict):
        if (isinstance(thesis.get("current_price"), (int, float))
                and thesis["current_price"] != price):
            thesis["current_price"] = price
            changed.append("investment_thesis.current_price")
        base = (doc.get("valuation") or {}).get("base") or {}
        base_iv = base.get("intrinsic_value")
        if "upside_base" in thesis and isinstance(base_iv, (int, float)):
            pct = f"{round((base_iv / price - 1.0) * 100.0):.0f}%"
            if thesis["upside_base"] != pct:
                thesis["upside_base"] = pct
                changed.append("investment_thesis.upside_base")

    return changed


# `const dcfData = {...};` -- the embedded copy the dashboard renders from.
_EMBED_RE = re.compile(r"(const\s+dcfData\s*=\s*)(\{.*?\})(\s*;)", re.DOTALL)


def update_dashboard(path: pathlib.Path, previous_price: float,
                     doc: dict, *, apply: bool) -> bool:
    """Re-embed the DCF, but only where that is provably safe.

    Corpus reality: 93 of 107 dashboards embed strict JSON, 14 embed JS object
    literals with unquoted keys, and 62 have already drifted structurally from
    the JSON on disk. So the embedded blob is rewritten only when it parses as
    strict JSON *and* its own `current_price` still matches the pre-refresh
    stored price -- a per-file precondition that excludes the drifted ones
    automatically. Everything else is left byte-identical and flagged.
    """
    try:
        html = path.read_text()
    except OSError:
        return False
    m = _EMBED_RE.search(html)
    if not m:
        return False
    try:
        embedded = json.loads(m.group(2))
    except json.JSONDecodeError:
        return False                      # JS object literal: hands off
    if not isinstance(embedded, dict):
        return False
    if embedded.get("current_price") != previous_price:
        return False                      # already drifted from disk
    # The embedded blob is not always a stale mirror of the file. SUM.NZ's
    # carried a `valuation` block the on-disk DCF.json does not have, and
    # its JS reads dcfData.valuation twice, so overwriting removed the key
    # and broke the page; PNG.V lost `net_debt` the same way. Only replace
    # when the file carries everything the page already had.
    missing = set(embedded) - set(doc)
    if missing:
        return False
    if not apply:
        return True
    payload = json.dumps(doc, indent=2)
    path.write_text(html[:m.start()] + m.group(1) + payload + m.group(3)
                    + html[m.end():])
    return True


def _json_scalar(value: object) -> str:
    return json.dumps(value)


def _object_end(text: str, start: int) -> int:
    """Index just past the `{...}` or `[...]` opening at/after `start`.

    Bounds a nested search to its own parent object so a same-named key
    elsewhere in the file cannot be matched instead.
    """
    opens = {"{": "}", "[": "]"}
    i = start
    while i < len(text) and text[i] not in opens:
        if text[i] not in " \t\r\n:":
            return len(text)            # a scalar, not a container
        i += 1
    if i >= len(text):
        return len(text)
    closer, depth, in_str, esc = opens[text[i]], 0, False, False
    for j in range(i, len(text)):
        ch = text[j]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch in opens:
                depth += 1
            elif ch in ("}", "]"):
                depth -= 1
                if depth == 0:
                    return j if ch == closer else len(text)
    return len(text)


def rewrite_values(original: str, doc: dict, changed: list[str]) -> str:
    """Rewrite only the changed scalars, preserving the file's formatting.

    `json.dumps(indent=2)` re-flows inline arrays -- a corpus DCF carrying
    `"fcf_growth_rates": [22.0, 20.0, 17.0]` gets exploded onto five lines --
    which turned a 10-value price update into a 240-line diff and buried the
    real change. This tool exists to be reviewable at a glance, so the edit
    is surgical: each changed leaf is located by its own key and replaced in
    place, and the bookkeeping block is appended as a new top-level key.

    Falls back to a full re-serialisation if the surgical edit cannot be made
    safely -- a correct file with an ugly diff beats a corrupted one.
    """
    text = original
    for path in changed:
        parts = path.split(".")
        node: object = doc
        for part in parts:
            key = part.split("[")[0]
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = None
                break
        if node is None:
            continue
        # `upside` occurs once per scenario and `current_price` twice, so a
        # bare key match would overwrite every sibling with one value. Walk
        # the ancestor keys in order, each search starting after the previous
        # match, which pins the edit to the right enclosing object.
        #
        # The search must also stay INSIDE the parent: IPL.NZ has a "base"
        # key at line 92, well before the "valuation" block at line 162, and
        # an unbounded search latched onto that one and wrote the bear
        # scenario's upside into the base scenario.
        at, end = 0, len(text)
        for ancestor in parts[:-1]:
            found = text.find(f'"{ancestor}"', at, end)
            if found == -1:
                return json.dumps(doc, indent=2) + "\n"
            at = found + len(ancestor) + 2
            end = _object_end(text, at)
        pattern = re.compile(
            r'("' + re.escape(parts[-1]) + r'"\s*:\s*)(-?[\d.]+|"[^"]*")')
        m = pattern.search(text, at, end)
        if m is None:
            return json.dumps(doc, indent=2) + "\n"
        text = text[:m.start()] + m.group(1) + _json_scalar(node) \
            + text[m.end():]

    # Append the bookkeeping block before the final closing brace.
    block = doc.get("price_refresh")
    if block is not None and '"price_refresh"' not in text:
        body = json.dumps({"price_refresh": block}, indent=2)[2:-2]
        end = text.rstrip().rfind("}")
        if end == -1:
            return json.dumps(doc, indent=2) + "\n"
        head = text.rstrip()[:end].rstrip()
        sep = "" if head.endswith(",") else ","
        text = head + sep + "\n" + body + "\n}\n"

    # A surgical edit that no longer parses is a bug; fall back rather than
    # write a broken file.
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return json.dumps(doc, indent=2) + "\n"
    return text


def refresh(repo: pathlib.Path | str, ticker: str, price: float | None,
            currency: str | None = None, *, apply: bool = False) -> Result:
    """Rewrite one ticker's price-derived numbers from an already-fetched quote."""
    repo = pathlib.Path(repo)
    res = Result(ticker=ticker)

    if price is None or price <= 0:
        res.reason = f"refusing a non-positive price ({price})"
        return res

    path = _dcf_path(repo, ticker)
    try:
        original = path.read_text()
        doc = json.loads(original)
    except (OSError, json.JSONDecodeError) as e:
        res.reason = f"no usable DCF ({type(e).__name__})"
        return res

    stored = doc.get("current_price")
    if not isinstance(stored, (int, float)) or stored <= 0:
        res.reason = "DCF has no usable current_price to compare against"
        return res

    want = _canon_currency(currency)
    have = _canon_currency(doc.get("currency"))
    if want and have and want != have:
        # WISE.L quotes GBP pence against USD filings; 885.6/48.43 yields a
        # plausible P/E of 18.3 that is pure coincidence.
        res.reason = f"currency mismatch: quote {want} vs DCF {have}"
        return res

    conflict = denomination_conflict(doc, float(price))
    if conflict:
        res.reason = conflict
        return res

    res.previous_price = float(stored)
    res.current_price = float(price)
    res.drift_pct = abs(price / stored - 1.0) * 100.0

    prose = find_prose_paths(doc, price_spellings(float(stored)))
    res.prose_paths = prose

    changed = apply_price(doc, float(price))
    res.fields_updated = changed
    res.would_change = bool(changed)
    res.ok = True

    if not changed:
        res.reason = "already current"
        return res
    if not apply:
        res.reason = "check only: nothing written"
        return res

    dash = repo / "research" / ticker / "Reports" / f"{ticker}_Dashboard.html"
    embeddable = update_dashboard(dash, float(stored), doc, apply=False)

    doc["price_refresh"] = {
        "refreshed_at": dt.datetime.now(dt.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "price_source": "yahoo:regularMarketPrice",
        "previous_price": res.previous_price,
        "current_price": res.current_price,
        "drift_pct": round(res.drift_pct, 1),
        "valuation_date": doc.get("valuation_date"),
        "fields_updated": changed,
        "prose_is_stale": bool(prose),
        "prose_paths_quoting_previous_price": prose,
        "dashboard_stale": not embeddable and dash.exists(),
    }

    path.write_text(rewrite_values(original, doc, changed))
    if embeddable:
        res.dashboard_updated = update_dashboard(dash, float(stored), doc,
                                                 apply=True)
    res.reason = "updated"
    return res


def refresh_ticker(repo: pathlib.Path | str, ticker: str, *,
                   apply: bool = False) -> Result:
    """Fetch a live quote and refresh, refusing to write on any fetch failure.

    Uses prune_queue.quote, which distinguishes a 404 (real evidence about the
    symbol) from a 429/5xx/network error (evidence about nothing). screen.py's
    fetcher collapses every failure to None, which under write-back would make
    a rate-limit blip indistinguishable from a genuine quote.
    """
    price, info = quote(ticker)
    if price is None:
        return Result(ticker=ticker, reason=f"no quote: {info}")
    return refresh(repo, ticker, price, info, apply=apply)


def _researched(repo: pathlib.Path) -> list[str]:
    root = repo / "research"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if (p / "Reports" / f"{p.name}_DCF.json").exists())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ticker", default="")
    p.add_argument("--all", action="store_true")
    p.add_argument("--apply", action="store_true",
                   help="write changes (default is a dry run)")
    p.add_argument("--check", action="store_true",
                   help="explicit dry run (the default)")
    p.add_argument("--drift-pct", type=float, default=0.0,
                   help="skip tickers whose price moved less than this")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help="seconds between quotes (Yahoo rate-limits)")
    args = p.parse_args()

    if not args.ticker and not args.all:
        p.error("pass --ticker TICKER or --all")

    names = [args.ticker] if args.ticker else _researched(REPO)
    apply = args.apply and not args.check

    updated = skipped = failed = 0
    stale_prose: list[str] = []
    for i, ticker in enumerate(names):
        if i:
            time.sleep(args.delay)
        res = refresh_ticker(REPO, ticker, apply=apply)
        if not res.ok:
            failed += 1
            print(f"  {ticker:12s} skipped: {res.reason}")
            continue
        if not res.would_change or (res.drift_pct or 0) < args.drift_pct:
            skipped += 1
            continue
        updated += 1
        flag = "" if apply else "  (dry run)"
        dash = " +dashboard" if res.dashboard_updated else ""
        print(f"  {ticker:12s} {res.previous_price} -> {res.current_price} "
              f"({res.drift_pct:+.1f}%)  {len(res.fields_updated)} fields"
              f"{dash}{flag}")
        if res.prose_paths:
            stale_prose.append(f"{ticker} ({len(res.prose_paths)})")

    verb = "updated" if apply else "would update"
    print(f"\n  {verb}: {updated}   unchanged: {skipped}   skipped: {failed}")
    if stale_prose:
        print(f"  prose now stale in: {', '.join(stale_prose)}")
        print("  (numbers are correct; the sentences quoting the old price "
              "are not)")
    if not apply:
        print("  (--check: nothing written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
