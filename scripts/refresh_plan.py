#!/usr/bin/env python3
"""Decide how much work a stale ticker actually needs, before spending on it.

Re-running /research-stock on an already-researched ticker costs the same as
researching a new one -- ~32 min and ~$5-8 -- because the skill carries four
"Always regenerate" directives and fires all four heavyweight subagents
regardless of whether anything changed.

Measured on this corpus: of 20 tickers stale by `valuation_date`, 19 had no
new filings at all. Only 0285.HK did. The other 19 were stale in price and
narrative only, and their financial data was already current. Sending those
through the parser re-derives numbers that cannot have moved.

So staleness is split into tiers, and cost is matched to what changed:

    3  new filings exist         -> full re-research (the parser must run)
    2  stale, but no new data    -> qualitative/narrative refresh only
    0  price drift only          -> free deterministic write-back
    1  nothing changed           -> no work

DELIBERATE NON-GOALS: this module is pure. It performs no network I/O and
writes nothing -- the live price is passed in, so the gate stays testable
without mocking a quote endpoint. Acting on a plan is refresh_price.py's job.

Usage:
  refresh_plan.py --all
  refresh_plan.py --ticker DCBO
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import re
from dataclasses import dataclass

import periods

REPO = pathlib.Path(__file__).resolve().parents[1]

# Matches the screen-investments skill and filter_tickers, so "stale" means
# the same thing everywhere.
DEFAULT_STALE_DAYS = 45

# Matches screen.py's --drift-pct: the threshold at which a stored price is
# too far from the market for its upside to be worth reading.
DEFAULT_DRIFT_PCT = 15.0

# {TICKER}_{TYPE}_{PERIOD}.txt, tolerating the `_Part2` split-file and `_d2`
# de-duplication suffixes. Without stripping those, 271 corpus files fail to
# parse and silently drop out of the comparison.
_FILING_RE = re.compile(
    r"^(?P<ticker>.+?)_(?P<kind>[A-Za-z0-9]+)_(?P<period>.+?)"
    r"(?:_Part\d+|_d\d+)?\.txt$"
)


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    """What one ticker needs, and the evidence behind that call."""

    ticker: str
    tier: int
    reason: str
    age_days: int | None
    drift_pct: float | None
    newest_filing: str | None
    newest_csv: str | None


def period_identity(p: periods.Period | None) -> tuple[int, int] | None:
    """Chronological identity: (fiscal_year, sub_rank), or None if undated.

    Deliberately NOT `periods.sort_key`. That key ties on the raw uppercased
    label, so `Q1-2026` > `Q1 2026` because '-' sorts above ' ' -- a spelling
    difference, not a chronological one. Comparing filenames (which use one
    convention) against CSV rows (which use another) through sort_key
    reported 59 tickers as having new data; 53 of those were false.

    Returns None for OTHER/undated periods so a presentation labelled
    `ASM-FY2024` can never become a phantom newest filing.
    """
    if p is None or p.fiscal_year is None or p.ptype == "OTHER":
        return None
    return (p.fiscal_year, periods.sub_rank(p.ptype))


def _newest(labels: list[str]) -> periods.Period | None:
    """The chronologically latest dated label, or None if none are dated."""
    best: periods.Period | None = None
    best_id: tuple[int, int] | None = None
    for label in labels:
        parsed = periods.parse(label)
        ident = period_identity(parsed)
        if ident is None:
            continue
        if best_id is None or ident > best_id:
            best, best_id = parsed, ident
    return best


def newest_extracted_period(repo: pathlib.Path,
                            ticker: str) -> periods.Period | None:
    """The latest period among this ticker's extracted filings."""
    folder = repo / "research" / ticker / "Extracted"
    if not folder.is_dir():
        return None
    matches = (_FILING_RE.match(p.name) for p in folder.glob("*.txt"))
    return _newest([m.group("period") for m in matches if m])


def newest_csv_period(repo: pathlib.Path,
                      ticker: str) -> periods.Period | None:
    """The latest period present in this ticker's Metrics CSV."""
    path = repo / "research" / ticker / "Reports" / f"{ticker}_Metrics.csv"
    try:
        text = path.read_text()
    except OSError:
        return None
    rows = list(csv.DictReader(text.splitlines()))
    return _newest([str(r.get("Period") or "") for r in rows])


def has_new_filings(repo: pathlib.Path, ticker: str) -> bool:
    """True when a filing on disk covers a period the CSV stops short of.

    A missing CSV counts as new data: there is nothing to compare against, so
    the parser has demonstrably not run.
    """
    filing = period_identity(newest_extracted_period(repo, ticker))
    if filing is None:
        return False
    parsed = period_identity(newest_csv_period(repo, ticker))
    if parsed is None:
        return True
    return filing > parsed


def _valuation_date(repo: pathlib.Path, ticker: str) -> dt.date | None:
    """The DCF's own valuation_date, or None if absent/unparseable."""
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


def stored_price(repo: pathlib.Path, ticker: str) -> float | None:
    """The price the DCF's upside figures were computed against."""
    path = repo / "research" / ticker / "Reports" / f"{ticker}_DCF.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("current_price")
    return float(value) if isinstance(value, (int, float)) else None


def plan_tier(repo: pathlib.Path | str, ticker: str, *,
              stale_days: int = DEFAULT_STALE_DAYS,
              drift_pct: float = DEFAULT_DRIFT_PCT,
              live_price: float | None = None) -> RefreshPlan:
    """Classify one ticker into a refresh tier.

    Order matters. New data outranks everything: no amount of price movement
    makes an un-parsed quarter unnecessary. Staleness outranks drift because a
    tier-2 narrative refresh subsumes the free numeric pass.
    """
    repo = pathlib.Path(repo)
    dcf = repo / "research" / ticker / "Reports" / f"{ticker}_DCF.json"

    filing = newest_extracted_period(repo, ticker)
    parsed = newest_csv_period(repo, ticker)
    labels = (filing.raw if filing else None, parsed.raw if parsed else None)

    when = _valuation_date(repo, ticker)
    age = (dt.date.today() - when).days if when else None

    stored = stored_price(repo, ticker)
    drift: float | None = None
    if live_price is not None and stored:
        drift = abs(live_price / stored - 1.0) * 100.0

    def plan(tier: int, reason: str) -> RefreshPlan:
        return RefreshPlan(ticker, tier, reason, age, drift, *labels)

    if not dcf.exists():
        return plan(3, "no DCF: never researched, or a run died partway")
    if has_new_filings(repo, ticker):
        newest = labels[0] or "?"
        return plan(3, f"new filing {newest} not in the CSV")
    if age is None:
        return plan(3, "no usable valuation_date")
    if age > stale_days:
        return plan(2, f"stale ({age}d) but no new filings: narrative only")
    if drift is not None and drift > drift_pct:
        return plan(0, f"price drift {drift:.1f}%: numeric write-back only")
    return plan(1, "nothing changed")


def _tickers(repo: pathlib.Path) -> list[str]:
    root = repo / "research"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ticker", default="", help="classify one ticker")
    p.add_argument("--all", action="store_true", help="classify every ticker")
    p.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    p.add_argument("--tier", type=int, default=None,
                   help="show only this tier")
    args = p.parse_args()

    names = [args.ticker] if args.ticker else _tickers(REPO)
    plans = [plan_tier(REPO, t, stale_days=args.stale_days) for t in names]
    if args.tier is not None:
        plans = [pl for pl in plans if pl.tier == args.tier]

    for pl in sorted(plans, key=lambda x: (-x.tier, x.ticker)):
        age = f"{pl.age_days}d" if pl.age_days is not None else "-"
        print(f"  tier {pl.tier}  {pl.ticker:12s} {age:>6s}  {pl.reason}")

    counts: dict[int, int] = {}
    for pl in plans:
        counts[pl.tier] = counts.get(pl.tier, 0) + 1
    summary = "  ".join(f"tier {t}: {counts[t]}" for t in sorted(counts))
    print(f"\n  {len(plans)} tickers   {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
