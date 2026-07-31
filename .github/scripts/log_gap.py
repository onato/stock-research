#!/usr/bin/env python3
"""Record something the automated pipeline could not do, for later review.

When financial-parser falls back to reading filings directly, that is
evidence build_facts.py is missing a pattern -- evidence which currently
evaporates when the run ends. This appends it to a durable log instead.

DELIBERATELY WRITE-ONLY. Agents record observations here; they never edit
build_facts.py, the skill, or their own instructions. Those files encode
hard-won corrections (the SEK.NZ peak-FCF failure, the DUOL SBC
double-count) and are shared across parallel runs -- a subagent rewriting
them mid-run is how that knowledge gets silently corrupted. A human reads
this log and decides what to change.

Usage (from an agent):
  log_gap.py --ticker AFC.NZ --kind missing_pattern \
             --metric OperatingCashFlow \
             --detail "filing says 'Net cash inflow from operating activities'" \
             --example "AFC.NZ_Annual_FY2026.txt:1042"

  log_gap.py --report            # grouped summary, most common first
  log_gap.py --report --metric EBITDA
"""

import argparse
import datetime as dt
import json
import pathlib
import sys
import collections

REPO = pathlib.Path(__file__).resolve().parents[2]
LOG = REPO / ".github" / "state" / "improvements.jsonl"

KINDS = (
    "missing_pattern",   # build_facts found no candidate for a metric that exists
    "wrong_candidate",   # candidates present but all wrong / misparsed
    "ambiguous_units",   # units could not be determined from the filing
    "period_unclear",    # filename/period mapping failed
    "layout_unparsed",   # filing structure the line regex cannot handle
    "other",
)


def add(args):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "ticker": args.ticker,
        "kind": args.kind,
        "metric": args.metric,
        "detail": args.detail,
        "example": args.example,
    }
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(f"logged: {args.kind} / {args.metric or '-'} ({args.ticker})")
    return 0


def report(args):
    if not LOG.exists():
        print("no gaps logged yet")
        return 0
    recs = []
    for line in open(LOG, errors="replace"):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if args.metric:
        recs = [r for r in recs if r.get("metric") == args.metric]
    if args.ticker:
        recs = [r for r in recs if r.get("ticker") == args.ticker]

    if not recs:
        print("no matching entries")
        return 0

    by_kind = collections.Counter(r.get("kind") for r in recs)
    by_metric = collections.Counter(r.get("metric") for r in recs if r.get("metric"))
    tickers = collections.Counter(r.get("ticker") for r in recs)

    print(f"{len(recs)} observation(s) across {len(tickers)} ticker(s)\n")
    print("  by kind:")
    for k, n in by_kind.most_common():
        print(f"    {n:4d}  {k}")
    print("\n  by metric (most-wanted patterns first):")
    for m, n in by_metric.most_common(12):
        # A metric missing across many tickers is a systemic gap, not a
        # one-off filing quirk -- fix those first.
        span = len({r["ticker"] for r in recs if r.get("metric") == m})
        print(f"    {n:4d}  {m:22s} ({span} ticker(s))")

    print("\n  most recent:")
    for r in recs[-6:]:
        print(f"    {r['ts'][:16]}  {r['ticker']:9s} {r.get('kind','?'):16s} "
              f"{r.get('metric') or ''}")
        if r.get("detail"):
            print(f"                      {r['detail'][:88]}")
        if r.get("example"):
            print(f"                      e.g. {r['example'][:70]}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticker", default="")
    p.add_argument("--kind", choices=KINDS, default="other")
    p.add_argument("--metric", default="")
    p.add_argument("--detail", default="")
    p.add_argument("--example", default="", help="file:line showing the case")
    p.add_argument("--report", action="store_true")
    args = p.parse_args()

    if args.report:
        return report(args)
    if not args.ticker:
        p.error("--ticker is required when logging (or use --report)")
    return add(args)


if __name__ == "__main__":
    sys.exit(main())
