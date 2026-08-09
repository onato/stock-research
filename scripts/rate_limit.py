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


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: rate_limit.py LOG [CAP_SECONDS]", file=sys.stderr)
        return 2
    try:
        cap = int(sys.argv[2])
    except (IndexError, ValueError):
        cap = DEFAULT_CAP
    print(seconds_to_wait(sys.argv[1], cap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
