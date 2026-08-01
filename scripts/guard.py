#!/usr/bin/env python3
"""Weekend + budget gate for the automated screener.

The screener is driven by a cron that fires repeatedly across the weekend.
Each run calls this first and bails out cheaply (a few seconds of free
runner time) unless BOTH hold:

  * today is Saturday or Sunday (UTC), and
  * fewer than --max-runs runs have already happened this weekend.

The counter lives in `state/budget.json`, keyed by the ISO
year+week, so it resets by itself each weekend with no cleanup step.

The count is incremented BEFORE the expensive research step, not after: a
run that crashes or times out must still consume its slot, otherwise one
persistently failing ticker would retry every cron tick and quietly drain
the token budget.

Writes `proceed=true|false` to $GITHUB_OUTPUT. Always exits 0 -- the
workflow branches on the output rather than on exit status, so a blocked
run is reported as a clean skip rather than a red X.
"""

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = REPO_ROOT / "state" / "budget.json"

SATURDAY, SUNDAY = 5, 6


def weekend_key(d):
    """ISO year+week, e.g. '2026-W31'.

    Saturday and Sunday share an ISO week number, so both days of one
    weekend map to the same key and draw from the same budget.
    """
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


@contextlib.contextmanager
def state_lock():
    """Serialize the budget read-modify-write across concurrent runners.

    Parallel local runs would otherwise each read the same count and write
    back the same increment, so N runs would consume one slot instead of N.
    The lock file is separate from the state file so the state stays a
    plain committed JSON document.

    On a GitHub runner there is only ever one process, so this is a no-op;
    flock is advisory and uncontended there.
    """
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_FILE.with_suffix(".lock")
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def emit(proceed, reason, runs_used, max_runs):
    out = os.environ.get("GITHUB_OUTPUT")
    payload = (
        f"proceed={'true' if proceed else 'false'}\n"
        f"runs_used={runs_used}\n"
        f"max_runs={max_runs}\n"
    )
    if out:
        with open(out, "a") as fh:
            fh.write(payload)
    print(payload, end="")
    print(reason, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-runs", type=int, default=8, help="Runs allowed per weekend")
    ap.add_argument(
        "--ignore-weekend",
        action="store_true",
        help="Skip the Sat/Sun check (manual dispatch). Budget still applies.",
    )
    ap.add_argument(
        "--ignore-budget",
        action="store_true",
        help="Skip the budget check AND do not increment (dry runs/testing).",
    )
    ap.add_argument(
        "--today",
        default="",
        help="Override today's date as YYYY-MM-DD (for local testing).",
    )
    args = ap.parse_args()

    if args.today:
        today = dt.datetime.strptime(args.today, "%Y-%m-%d").date()
    else:
        today = dt.datetime.now(dt.timezone.utc).date()

    key = weekend_key(today)

    if not args.ignore_weekend and today.weekday() not in (SATURDAY, SUNDAY):
        emit(
            False,
            f"Blocked: {today} is {today.strftime('%A')}, not a weekend (UTC).",
            int(load_state().get(key, 0)),
            args.max_runs,
        )
        return 0

    if args.ignore_budget:
        runs_used = int(load_state().get(key, 0))
        emit(True, f"Proceeding: budget check bypassed ({key}).", runs_used, args.max_runs)
        return 0

    # Read, check and claim atomically: with parallel runners, doing the
    # check outside the lock would let several runs all see the same count
    # and consume a single slot between them.
    with state_lock():
        state = load_state()
        runs_used = int(state.get(key, 0))

        if runs_used >= args.max_runs:
            emit(
                False,
                f"Blocked: weekend {key} has used {runs_used}/{args.max_runs} runs.",
                runs_used,
                args.max_runs,
            )
            return 0

        # Claim the slot up front so a crash still costs one run.
        state[key] = runs_used + 1
        save_state(state)

    emit(
        True,
        f"Proceeding: weekend {key} run {runs_used + 1}/{args.max_runs}.",
        runs_used + 1,
        args.max_runs,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
