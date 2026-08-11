#!/usr/bin/env python3
"""One row per ticker: how a parallel research run is going.

`run_loop.sh -j 4` merges four traces into one terminal. Per-line tags made
that output attributable but not readable -- you cannot follow a single ticker,
and a stalled one looks exactly like a busy one. This is the summary view that
answers "how is the batch going" without reading four traces at once, and it
is what the status pane in `watch_run.sh` renders.

Two columns carry most of the value:

  idle   A worker whose log has not moved in minutes is the stalled case that
         is otherwise invisible.
  cost   CEN.NZ cost $16.99 and CDI.NZ $12.86 on 2026-08-10, both recorded as
         successes, and the spend was not visible until the run was over.

Everything is derived from files the run already writes -- `state/logs/*.log`
and GNU parallel's joblog -- so there is nothing new to keep in sync. The
last-tool text comes from `progress.describe()`, the same renderer the pane
trace uses, so the two can never disagree.

Usage:
  run_status.py                 # print the table once
  run_status.py --watch         # redraw until interrupted
  run_status.py --watch --once  # one pass (used by the tests)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import progress

REPO = pathlib.Path(__file__).resolve().parents[1]

# research_one.sh's exit codes. 0 means the deliverables are on disk whatever
# the CLI said; 4 means a rate-limit window resets further out than one run
# can wait; 3 means a clean exit that produced nothing.
STATE_FOR_EXIT = {
    0: "done",
    3: "incomplete",
    4: "rate-limited",
    # Never attempted: the run halted on a rate limit first, so the ticker is
    # still queued rather than broken.
    5: "stood-down",
}

REDRAW_SECONDS = 5.0

# A transcript untouched for longer than this belongs to an earlier batch.
# 6h is comfortably longer than a slow ticker (~40min, worst seen 8.4h under
# the old rate-limit bug) while excluding yesterday's runs.
DEFAULT_SINCE = 6 * 3600

# Keep the table inside a narrow tmux pane.
LAST_TOOL_WIDTH = 46


def logs_dir(root: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(root) / "state" / "logs"


def discover(root: pathlib.Path) -> list[str]:
    """Tickers that have a transcript, alphabetically.

    Matches `*.log` only: the `.stream` files beside them are the rendered
    progress the panes tail, not transcripts.
    """
    d = logs_dir(root)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.log"))


def joblog_rows(path: pathlib.Path | str) -> dict[str, dict[str, Any]]:
    """{ticker: {"exit": int, "runtime": float}} from parallel's joblog.

    Column 7 is the exit status and column 9 the command, whose last field is
    the ticker. A short row is a run still in flight (or a truncated write)
    and is skipped rather than guessed at.
    """
    path = pathlib.Path(path)
    out: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return out
    for line in lines[1:]:                     # row 1 is the header
        parts = line.split("\t")
        if len(parts) < 9:
            continue
        ticker = parts[8].split()[-1] if parts[8].split() else ""
        if not ticker:
            continue
        try:
            out[ticker] = {"exit": int(parts[6]), "runtime": float(parts[3])}
        except ValueError:
            continue
    return out


def ticker_row(root: pathlib.Path, ticker: str,
               joblog: pathlib.Path | str | None = None,
               now: float | None = None) -> dict[str, Any]:
    """Everything the table shows for one ticker."""
    root = pathlib.Path(root)
    path = logs_dir(root) / f"{ticker}.log"
    now = time.time() if now is None else now

    row: dict[str, Any] = {
        "ticker": ticker, "state": "pending", "tools": 0,
        "last_tool": "", "cost": None, "idle": 0, "elapsed": None,
    }
    if not path.is_file():
        return row

    tools = 0
    last_tool = ""
    cost: float | None = None
    saw_result = False
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return row

    for raw in lines:
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "assistant":
            content = (event.get("message") or {}).get("content") or []
            if isinstance(content, list):
                for block in content:
                    if (isinstance(block, dict)
                            and block.get("type") == "tool_use"):
                        tools += 1
                        last_tool = progress.describe(block.get("name", "?"),
                                                      block.get("input"))
        elif kind == "result":
            saw_result = True
            got = event.get("total_cost_usd")
            if isinstance(got, (int, float)):
                cost = float(got)

    row.update({
        "tools": tools,
        "last_tool": last_tool,
        "cost": cost,
        "idle": max(0, int(now - path.stat().st_mtime)),
        "state": "done" if saw_result else "running",
    })

    # parallel's joblog is authoritative once the ticker has finished: a run
    # can end on an is_error result and still have produced everything, which
    # research_one.sh reports as exit 0.
    if joblog is not None:
        entry = joblog_rows(joblog).get(ticker)
        if entry is not None:
            row["elapsed"] = int(entry["runtime"])
            row["state"] = STATE_FOR_EXIT.get(entry["exit"], "failed")
    return row


def _fmt_secs(value: int | None) -> str:
    """m/s under an hour, h/m above it.

    state/logs/ keeps every transcript, so an unfiltered table printed idle
    times like "14488m55" -- ten days expressed in minutes, which reads as
    noise rather than information.
    """
    if value is None:
        return "-"
    if value >= 3600:
        return f"{value // 3600}h{(value % 3600) // 60:02d}m"
    return f"{value // 60}m{value % 60:02d}"


def render(root: pathlib.Path, joblog: pathlib.Path | str | None = None,
           now: float | None = None, since: float = DEFAULT_SINCE) -> str:
    """The whole table as text.

    `since` bounds it to the current run: a transcript untouched for longer
    than that is from a previous batch. 46 transcripts had accumulated, so
    without this the four tickers actually running were buried behind 42
    finished ones. A ticker named in the joblog is part of this run by
    definition and is kept regardless of age; `since=0` shows everything.
    """
    root = pathlib.Path(root)
    if joblog is None:
        candidate = root / "state" / "joblog.tsv"
        joblog = candidate if candidate.is_file() else None

    tickers = discover(root)
    if not tickers:
        return "no run found (no transcripts in state/logs/)"

    in_run = set(joblog_rows(joblog)) if joblog is not None else set()
    rows = [ticker_row(root, t, joblog, now) for t in tickers]
    if since:
        rows = [r for r in rows
                if r["idle"] <= since or r["ticker"] in in_run]
    if not rows:
        return "no run found (no recent activity in state/logs/)"
    # Active tickers first -- they are the ones worth watching.
    order = {"running": 0, "pending": 1, "rate-limited": 2, "incomplete": 3,
             "failed": 4, "stood-down": 5, "done": 6}
    rows.sort(key=lambda r: (order.get(r["state"], 9), r["ticker"]))

    head = (f"{'TICKER':<10} {'STATE':<13} {'ELAPSED':>8} {'IDLE':>7} "
            f"{'TOOLS':>6} {'COST':>8}  LAST")
    out = [head, "-" * len(head)]
    total = 0.0
    for r in rows:
        if r["cost"]:
            total += r["cost"]
        cost = f"${r['cost']:.2f}" if r["cost"] is not None else "-"
        last = r["last_tool"][:LAST_TOOL_WIDTH]
        out.append(f"{r['ticker']:<10} {r['state']:<13} "
                   f"{_fmt_secs(r['elapsed']):>8} {_fmt_secs(r['idle']):>7} "
                   f"{r['tools']:>6} {cost:>8}  {last}")

    active = sum(1 for r in rows if r["state"] == "running")
    out.append("-" * len(head))
    out.append(f"{len(rows)} ticker(s), {active} running, "
               f"total ${total:.2f}")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(REPO))
    p.add_argument("--joblog", default=None,
                   help="default: {root}/state/joblog.tsv when it exists")
    p.add_argument("--watch", action="store_true", help="redraw on a timer")
    p.add_argument("--interval", type=float, default=REDRAW_SECONDS)
    p.add_argument("--once", action="store_true",
                   help="with --watch, draw a single pass and exit")
    p.add_argument("--since", type=float, default=DEFAULT_SINCE,
                   help="ignore transcripts idle longer than this many "
                        "seconds; 0 shows every run ever recorded")
    args = p.parse_args()

    root = pathlib.Path(args.root)
    if not args.watch:
        print(render(root, args.joblog, since=args.since))
        return 0

    while True:
        # \x1b[H puts the cursor home and \x1b[J clears below it, so the table
        # redraws in place without the flicker of a full clear.
        print("\x1b[H\x1b[J" + render(root, args.joblog, since=args.since),
              flush=True)
        if args.once:
            return 0
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
