#!/usr/bin/env python3
"""Comment out delisted tickers in the queue, before they cost a research run.

ACE.NZ cost $1.20 for the model to discover it had been renamed to BAI.NZ
in April 2024. The NZX list dates from January 2026, so more are stale.
One Yahoo quote each settles it for free.

Also handles the case that made ACE.NZ keep resurfacing: a ticker whose
research bailed out leaves an empty Reports/ directory, and the selector
treats that as "a previous run died, retry it". For a genuinely delisted
name that is an infinite retry, so commenting it out is the fix.

Nothing is deleted -- entries are commented with the reason, so the file
still records what was considered.

Usage:
  prune_queue.py              # report what would be commented out
  prune_queue.py --apply      # write the queue files
  prune_queue.py --file queue/nzx.txt
"""

import argparse
import json
import pathlib
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
QUEUE = REPO / "queue"
UA = "Mozilla/5.0 (stock-research)"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=5d&interval=1d"


def quote(ticker: str) -> tuple[float | None, str]:
    """Return (price, currency) or (None, reason)."""
    try:
        req = urllib.request.Request(URL.format(t=ticker), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        # 404 means Yahoo has no such symbol -- that IS delisting evidence.
        # Other HTTP errors (429 rate limit, 5xx) say nothing about the
        # ticker, so they must not cause a live name to be commented out.
        if e.code == 404:
            return None, "no such symbol (404)"
        return None, f"fetch failed: HTTP {e.code}"
    except Exception as e:
        # A network failure is not evidence of delisting.
        return None, f"fetch failed: {str(e)[:40]}"

    err = (d.get("chart") or {}).get("error")
    if err:
        code = err.get("code", "") if isinstance(err, dict) else str(err)
        return None, f"no data ({code})"
    res = (d.get("chart") or {}).get("result") or []
    if not res:
        return None, "no data"
    meta = res[0].get("meta") or {}
    px = meta.get("regularMarketPrice")
    if px is None:
        return None, "no price"
    return px, meta.get("currency", "?")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the queue files (default: report only)")
    ap.add_argument("--file", default="")
    ap.add_argument("--delay", type=float, default=0.4,
                    help="seconds between requests (Yahoo rate-limits)")
    args = ap.parse_args()

    files = ([pathlib.Path(args.file)] if args.file
             else sorted(QUEUE.glob("*.txt")))
    dead, live, unknown = [], 0, []

    for f in files:
        if not f.exists():
            continue
        lines = f.read_text().splitlines()
        out = []
        changed = False
        for line in lines:
            stripped = line.split("#", 1)[0].strip()
            if not stripped:
                out.append(line)
                continue
            px, info = quote(stripped)
            time.sleep(args.delay)
            if px is not None:
                live += 1
                out.append(line)
                continue
            if "fetch failed" in info:
                unknown.append((stripped, info))
                out.append(line)          # keep it; we did not learn anything
                continue
            dead.append((stripped, info))
            out.append(f"# {line}  # delisted? {info}")
            changed = True

        if changed and args.apply:
            f.write_text("\n".join(out) + "\n")

    print(f"  live: {live}   delisted: {len(dead)}   unresolved: {len(unknown)}")
    if dead:
        print("\n  commented out:")
        for t, why in dead:
            print(f"    {t:12s} {why}")
    if unknown:
        print("\n  could not check (left in the queue):")
        for t, why in unknown[:8]:
            print(f"    {t:12s} {why}")
    if not args.apply:
        print("\n  (nothing written; pass --apply to comment them out)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
