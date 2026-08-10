#!/usr/bin/env python3
"""Decide whether a finished run must wait for a rate-limit window.

A run logs three kinds of `rate_limit_event`, and only one means requests are
actually being refused:

    allowed           fine
    allowed_warning   "you are at 79% of the seven-day quota" -- the request
                      still went through
    rejected          actually blocked

Treating `allowed_warning` as a block cost two hours per ticker. The seven-day
window's `resetsAt` is days away, so the computed wait clamped to the 2h
ceiling and the loop parked itself while quota remained: a batch run looked
hung when nothing was wrong. Only `rejected` waits.

Lives here rather than inside a lib.sh heredoc so it can be tested; the shell
calls it and sleeps for however many seconds come back.

Usage: rate_limit.py LOG [CAP_SECONDS]   # prints seconds to sleep, 0 = none
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

# Statuses that mean the request was refused. Anything else -- including
# `allowed_warning` -- means work is still getting through.
BLOCKING = {"rejected"}

DEFAULT_CAP = 7200          # 2h: a bogus timestamp must not park the loop
SLACK = 30                  # waking exactly on the boundary tends to re-reject


def seconds_to_wait(log: pathlib.Path | str,
                    cap: int = DEFAULT_CAP,
                    now: float | None = None) -> int:
    """How long to sleep before retrying, or 0 to proceed immediately."""
    path = pathlib.Path(log)
    if not path.is_file():
        return 0

    reset: float | None = None
    try:
        raw_lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return 0
    for raw in raw_lines:
        line = raw.strip()
        if line.startswith("{") and "rate_limit_event" in line:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "rate_limit_event":
                continue
            info = event.get("rate_limit_info") or {}
            if info.get("status") not in BLOCKING:
                continue
            at = info.get("resetsAt")
            if isinstance(at, (int, float)) and not isinstance(at, bool):
                # A run may log several rejections; wait out the furthest.
                reset = at if reset is None else max(reset, at)

    if reset is None:
        return 0
    remaining = int(reset - (time.time() if now is None else now)) + SLACK
    return max(0, min(cap, remaining))


def reset_at(log: pathlib.Path | str) -> float | None:
    """When the blocking window resets, or None if nothing was rejected."""
    path = pathlib.Path(log)
    if not path.is_file():
        return None
    try:
        raw_lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    latest: float | None = None
    for raw in raw_lines:
        line = raw.strip()
        if not line.startswith("{") or "rate_limit_event" not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "rate_limit_event":
            continue
        info = event.get("rate_limit_info") or {}
        if info.get("status") not in BLOCKING:
            continue
        at = info.get("resetsAt")
        if isinstance(at, (int, float)) and not isinstance(at, bool):
            latest = at if latest is None else max(latest, at)
    return latest


def is_unreachable(log: pathlib.Path | str, cap: int = DEFAULT_CAP,
                   now: float | None = None) -> bool:
    """True when the window resets further out than a single run can wait.

    The weekly quota resets days away, so sleeping the cap and retrying only
    re-rejects -- CCC.NZ burned 8.4 hours and $11.96 that way, redoing a
    ticker whose deliverables were already on disk. The caller should stop
    the run and resume after the reset instead of sleeping into a guaranteed
    rejection.
    """
    when = reset_at(log)
    if when is None:
        return False
    return (when - (time.time() if now is None else now)) > cap


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: rate_limit.py LOG [CAP_SECONDS]", file=sys.stderr)
        return 2
    if "--unreachable" in sys.argv:
        # Exit 0 when the run should stop; the shell branches on this.
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        cap = int(args[1]) if len(args) > 1 else DEFAULT_CAP
        when = reset_at(args[0])
        if when is not None and is_unreachable(args[0], cap):
            print(int(when))
            return 0
        return 1
    try:
        cap = int(sys.argv[2])
    except (IndexError, ValueError):
        cap = DEFAULT_CAP
    print(seconds_to_wait(sys.argv[1], cap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
