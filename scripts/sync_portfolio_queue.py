#!/usr/bin/env python3
"""Derive queue/priority.txt from the portfolio tracker.

The priority queue is "what I hold, then what I watch". That list lives in
../portfolio-tracker/data/user_portfolio.json and changes whenever a position
is opened or closed; a hand-kept copy drifts (6995.T was bought, DCBO and
ADYEN.AS were watched, none were queued). So:

* `select_ticker.py` reads the tracker LIVE through `portfolio_tickers()`
  whenever the sibling repo is present, so nothing needs re-running locally.
* This CLI regenerates the committed `queue/priority.txt` for CI, where the
  sibling repo is absent. `make run` calls it, so the fallback tracks the
  tracker at every local run. Without a tracker it leaves the file alone.

Usage:
  sync_portfolio_queue.py                     # default paths
  sync_portfolio_queue.py --portfolio P --out Q
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PORTFOLIO = (REPO_ROOT.parent / "portfolio-tracker" / "data"
                     / "user_portfolio.json")
DEFAULT_OUT = REPO_ROOT / "queue" / "priority.txt"

Tagged = list[tuple[str, str]]


def _is_held(position: dict) -> bool:
    """Net lot quantity > 0. No lot history at all still counts as held:
    presence in `positions` is the signal; absence of lots is not a sale."""
    lots = position.get("lots")
    if not lots:
        return True
    total = 0.0
    for lot in lots:
        try:
            total += float(lot.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total > 0


def portfolio_tickers(path: pathlib.Path | str) -> Tagged | None:
    """[(ticker, "held"|"watchlist")] in tracker order, held first.

    Returns None -- not [] -- when the tracker is missing or unreadable, so a
    caller can tell "no live source" from "the portfolio is empty". The
    distinction matters: [] would silently drop every priority ticker.
    """
    path = pathlib.Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    out: Tagged = []
    seen: set[str] = set()
    for pos in data.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        ticker = str(pos.get("ticker") or "").strip()
        if ticker and ticker not in seen and _is_held(pos):
            out.append((ticker, "held"))
            seen.add(ticker)
    for item in data.get("watchlist") or []:
        raw = item.get("ticker") if isinstance(item, dict) else item
        ticker = str(raw or "").strip()
        if ticker and ticker not in seen:
            out.append((ticker, "watchlist"))
            seen.add(ticker)
    return out


HEADER = """\
# Portfolio holdings and watchlist -- refreshed ahead of the
# broad exchange sweeps. GENERATED from
# ../portfolio-tracker/data/user_portfolio.json by
# scripts/sync_portfolio_queue.py (run by `make run`); do not hand-edit.
# Locally select_ticker.py reads the tracker live and ignores this file;
# it is the fallback for CI, where the sibling repo is absent.
"""


def render(tickers: Tagged) -> str:
    body = "".join(f"{t:<10} # {tag}\n" for t, tag in tickers)
    return HEADER + "\n" + body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--portfolio", default=str(DEFAULT_PORTFOLIO))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    tickers = portfolio_tickers(args.portfolio)
    if tickers is None:
        # CI, or the sibling repo is not checked out: keep the committed copy.
        print(f"sync_portfolio_queue: no portfolio at {args.portfolio}; "
              f"leaving {args.out} unchanged.", file=sys.stderr)
        return 0

    out = pathlib.Path(args.out)
    text = render(tickers)
    if out.exists() and out.read_text() == text:
        print(f"sync_portfolio_queue: {out} already current "
              f"({len(tickers)} tickers).", file=sys.stderr)
        return 0
    out.write_text(text)
    held = sum(1 for _, tag in tickers if tag == "held")
    print(f"sync_portfolio_queue: wrote {out} -- {held} held, "
          f"{len(tickers) - held} watchlist.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
