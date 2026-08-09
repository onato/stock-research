#!/usr/bin/env python3
"""Reduce a supplied ticker list to the ones still worth researching.

`select_ticker.py` applies the policy -- new tickers first, then refresh the
stalest -- but `run_loop.sh` only consulted it when invoked with no arguments.
Passing tickers explicitly bypassed every filter, so

    run_loop.sh -j 4 -n 20 $(cat state/backlog.txt)

re-ran six already-finished tickers, silently ignored `-n 20`, and scheduled
all 782 entries -- including a prose GAP note that `$(cat)` word-split into
half a dozen fake tickers.

This applies the same policy to an explicit list, so both paths behave the
same way. Ordering matches select_ticker's: unresearched first (in the order
supplied), then researched-but-stale, oldest valuation first.

Usage:
  filter_tickers.py [--limit N] [--stale-days N] [--force] TICKER...
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]

# Exchange-suffixed or bare symbols. Anything else in a queue file is prose:
# state/backlog.txt carries a "GAP: ..." note that must never be scheduled.
TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}(\.[A-Z]{1,3})?$")

# Matches the screen-investments skill's default, so "stale" means the same
# thing here as it does in the screener.
DEFAULT_STALE_DAYS = 45


def looks_like_ticker(text: str) -> bool:
    return bool(TICKER_RE.match((text or "").strip().upper()))


def _valuation_date(repo: pathlib.Path, ticker: str) -> dt.date | None:
    """The DCF's own valuation_date, or None if absent/unparseable.

    Deliberately not file mtime: a checkout stamps every file with the
    checkout time, which would make an mtime ranking arbitrary.
    """
    path = repo / "research" / ticker / "Reports" / f"{ticker}_DCF.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    text = str(data.get("valuation_date") or "")[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def age_days(repo: pathlib.Path, ticker: str) -> int | None:
    """How old this ticker's research is, or None if it has none.

    None means "never researched, or researched without producing a dated
    valuation" -- both are work to do, so both sort ahead of stale refreshes.
    """
    reports = repo / "research" / ticker / "Reports"
    # An empty Reports/ means a previous run died partway through; it stays
    # eligible rather than being skipped forever.
    if not reports.is_dir() or not any(reports.iterdir()):
        return None
    when = _valuation_date(repo, ticker)
    if when is None:
        return None
    return (dt.date.today() - when).days


def eligible(repo: pathlib.Path | str, tickers: list[str], *,
             limit: int = 0, stale_days: int = DEFAULT_STALE_DAYS,
             force: bool = False) -> list[str]:
    """The supplied tickers still worth researching, in priority order."""
    repo = pathlib.Path(repo)

    seen: set[str] = set()
    clean: list[str] = []
    for raw in tickers:
        ticker = (raw or "").strip()
        if not looks_like_ticker(ticker) or ticker in seen:
            continue
        seen.add(ticker)
        clean.append(ticker)

    fresh: list[str] = []
    stale: list[tuple[int, str]] = []
    for ticker in clean:
        age = age_days(repo, ticker)
        if age is None:
            fresh.append(ticker)          # never researched: highest priority
        elif force or age >= stale_days:
            stale.append((age, ticker))

    # Oldest valuation first among refreshes; supplied order among new ones.
    stale.sort(key=lambda pair: (-pair[0], pair[1]))
    out = fresh + [ticker for _, ticker in stale]
    return out[:limit] if limit > 0 else out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("tickers", nargs="*")
    p.add_argument("--limit", type=int, default=0,
                   help="keep at most N (applied after filtering)")
    p.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS,
                   help=f"refresh research older than this (default "
                        f"{DEFAULT_STALE_DAYS})")
    p.add_argument("--force", action="store_true",
                   help="keep researched tickers regardless of age")
    args = p.parse_args()

    for ticker in eligible(REPO, args.tickers, limit=args.limit,
                           stale_days=args.stale_days, force=args.force):
        print(ticker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
