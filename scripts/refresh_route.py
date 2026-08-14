#!/usr/bin/env python3
"""Map a ticker's refresh tier to the skill that should actually run.

`refresh_plan.py` has computed tiers 0-3 since it was written, but only its
`has_new_filings` helper was ever consumed. `plan_tier` had no caller outside
its own CLI, so the tier that most needed a destination -- tier 2, stale by
date with no new filings -- had nowhere to go.

That left a stale ticker with two options: a free deterministic price
write-back, or a ~$6 / ~32 min full re-research. Everything in tier 2 falls
between them. Worse, `make run --require-new-filings` skips tier 2 entirely,
so those tickers never come up at all: 23 of 121 sit there, and DCBO had been
stale 48 days with its financials already current.

The routing:

    3  new filings, or never researched  -> /research-stock  (parser must run)
    2  stale, no new data                -> /refresh-stock   (narrative + DCF)
    1  nothing changed                   -> no model
    0  price drift only                  -> no model (refresh_price.py)

Tier 2 skips the ir-scraper, pdf-processor and financial-parser stages. That
is safe precisely because `has_new_filings` is false: the same filings through
the same parser produce the same CSV. It is also where the money is -- the
parser is the most expensive agent in the pipeline (183 model turns and 18.2M
cache-read tokens on one ticker, ~60% of its cost).

DELIBERATE NON-GOALS: this module is pure and performs no network I/O -- the
live price is passed in, mirroring refresh_plan. It decides *what to run*, and
never runs it.

Usage:
  refresh_route.py --ticker DCBO          # human-readable
  refresh_route.py --ticker DCBO --prompt # the exact CLI prompt, for lib.sh
"""

from __future__ import annotations

import argparse
import pathlib
from dataclasses import dataclass

import refresh_plan

REPO = pathlib.Path(__file__).resolve().parents[1]

FULL = "research-stock"
CHEAP = "refresh-stock"

# Tier -> skill. Absent means no model runs at all.
_SKILL: dict[int, str] = {3: FULL, 2: CHEAP}


@dataclass(frozen=True, slots=True)
class Route:
    """What to run for one ticker, and why."""

    ticker: str
    tier: int
    skill: str | None
    reason: str


def route(repo: pathlib.Path | str, ticker: str, *,
          live_price: float | None = None,
          stale_days: int = refresh_plan.DEFAULT_STALE_DAYS,
          force: bool = False) -> Route:
    """Classify `ticker` and name the skill that should handle it.

    `force` pins the answer to the full re-research regardless of tier, for
    `make run TICKER=X FORCE=1` -- an operator asking for the expensive path
    explicitly should get it.
    """
    repo = pathlib.Path(repo)
    plan = refresh_plan.plan_tier(repo, ticker, stale_days=stale_days,
                                  live_price=live_price)
    if force:
        return Route(ticker, plan.tier, FULL, "forced by operator")
    return Route(ticker, plan.tier, _SKILL.get(plan.tier), plan.reason)


def prompt(repo: pathlib.Path | str, ticker: str, *,
           live_price: float | None = None,
           stale_days: int = refresh_plan.DEFAULT_STALE_DAYS,
           force: bool = False) -> str:
    """The CLI prompt for this ticker, or "" when no model should run.

    Empty is the skip signal. It must never degrade to a bare skill name: a
    prompt of "/research-stock" with no ticker would research whatever the
    model felt like.
    """
    r = route(repo, ticker, live_price=live_price, stale_days=stale_days,
              force=force)
    return f"/{r.skill} {ticker}" if r.skill else ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ticker", nargs="?", default="")
    p.add_argument("--ticker", dest="ticker_opt", default="")
    p.add_argument("--prompt", action="store_true",
                   help="print only the CLI prompt (empty means skip)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--stale-days", type=int,
                   default=refresh_plan.DEFAULT_STALE_DAYS)
    args = p.parse_args()

    ticker = args.ticker_opt or args.ticker
    if not ticker:
        p.error("a ticker is required")

    if args.prompt:
        print(prompt(REPO, ticker, stale_days=args.stale_days,
                     force=args.force))
        return 0

    r = route(REPO, ticker, stale_days=args.stale_days, force=args.force)
    skill = f"/{r.skill}" if r.skill else "(no model)"
    print(f"  {r.ticker:12s} tier {r.tier}  {skill:18s} {r.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
