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

  log_gap.py --report            # grouped summary of OPEN entries
  log_gap.py --report --metric EBITDA
  log_gap.py --list              # open entries, numbered by file line
  log_gap.py --list --all        # include resolved ones

Closure (for the human review loop) keeps the file append-only: resolving
appends a {"resolves": <line>, "note": ...} record instead of editing the
entry, so history survives while reports show only the open backlog:

  log_gap.py --resolve 12 --note "handled by parsers/lse.py units fix"
"""

import argparse
import collections
import contextlib
import datetime as dt
import json
import pathlib
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
LOG = REPO / "state" / "improvements.jsonl"

KINDS = (
    "missing_pattern",   # build_facts found no candidate for a metric that exists
    "wrong_candidate",   # candidates present but all wrong / misparsed
    "ambiguous_units",   # units could not be determined from the filing
    "period_unclear",    # filename/period mapping failed
    "layout_unparsed",   # filing structure the line regex cannot handle
    "other",
)


def add(args: argparse.Namespace) -> int:
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


def load() -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                    dict[int, dict[str, Any]]]:
    """(open_entries, resolutions) with file line numbers attached.

    An entry is open unless a later {"resolves": <line>} record names its
    line. Resolutions are appended, never edited in place — the log stays
    append-only for agents and history survives closure.
    """
    entries: list[dict[str, Any]]
    resolutions: dict[int, dict[str, Any]]
    entries, resolutions = [], {}
    if LOG.exists():
        with open(LOG, errors="replace") as fh:
            for lineno, raw in enumerate(fh, 1):
                if not raw.strip():
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    rec = json.loads(raw)
                    if "resolves" in rec:
                        resolutions[int(rec["resolves"])] = rec
                    else:
                        rec["_line"] = lineno
                        entries.append(rec)
    open_ = [r for r in entries if r["_line"] not in resolutions]
    return entries, open_, resolutions


def resolve(args: argparse.Namespace) -> int:
    entries, _open, resolutions = load()
    target = int(args.resolve)
    if target in resolutions:
        print(f"entry #{target} is already resolved "
              f"({resolutions[target].get('note', '')})", file=sys.stderr)
        return 1
    if not any(r["_line"] == target for r in entries):
        print(f"no gap entry at line {target} (see --list)", file=sys.stderr)
        return 1
    rec = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "resolves": target,
        "note": args.note,
    }
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(f"resolved #{target}: {args.note}")
    return 0


def list_entries(args: argparse.Namespace) -> int:
    entries, open_, resolutions = load()
    show = entries if args.all else open_
    if args.ticker:
        show = [r for r in show if r.get("ticker") == args.ticker]
    if not show:
        print("no open entries" if not args.all else "no entries")
        return 0
    for r in show:
        res = resolutions.get(r["_line"])
        mark = f"  [resolved: {res.get('note', '')}]" if res else ""
        print(f"#{r['_line']:<4d} {r.get('ts', '')[:16]}  {r.get('ticker', ''):9s} "
              f"{r.get('kind', '?'):16s} {r.get('metric') or '-'}{mark}")
        if r.get("detail"):
            print(f"      {r['detail'][:100]}")
        if r.get("example"):
            print(f"      e.g. {r['example'][:80]}")
    return 0


def report(args: argparse.Namespace) -> int:
    _, recs, resolutions = load()
    if args.metric:
        recs = [r for r in recs if r.get("metric") == args.metric]
    if args.ticker:
        recs = [r for r in recs if r.get("ticker") == args.ticker]

    if not recs:
        print("no open entries"
              + (f" ({len(resolutions)} resolved; --list --all shows them)"
                 if resolutions else ""))
        return 0

    by_kind = collections.Counter(r.get("kind") for r in recs)
    by_metric = collections.Counter(r.get("metric") for r in recs if r.get("metric"))
    tickers = collections.Counter(r.get("ticker") for r in recs)

    resolved_note = f", {len(resolutions)} resolved" if resolutions else ""
    print(f"{len(recs)} open observation(s) across {len(tickers)} ticker(s)"
          f"{resolved_note}\n")
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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticker", default="")
    p.add_argument("--kind", choices=KINDS, default="other")
    p.add_argument("--metric", default="")
    p.add_argument("--detail", default="")
    p.add_argument("--example", default="", help="file:line showing the case")
    p.add_argument("--report", action="store_true")
    p.add_argument("--list", action="store_true", dest="list_",
                   help="numbered open entries (IDs for --resolve)")
    p.add_argument("--all", action="store_true",
                   help="with --list: include resolved entries")
    p.add_argument("--resolve", default="", metavar="N",
                   help="close entry #N (appends a resolution record)")
    p.add_argument("--note", default="", help="with --resolve: what fixed it")
    args = p.parse_args()

    if args.resolve:
        return resolve(args)
    if args.list_:
        return list_entries(args)
    if args.report:
        return report(args)
    if not args.ticker:
        p.error("--ticker is required when logging (or use --report)")
    return add(args)


if __name__ == "__main__":
    sys.exit(main())
