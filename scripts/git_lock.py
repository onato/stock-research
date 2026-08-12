#!/usr/bin/env python3
"""Is a held git lock live, or an orphan left by a dead worker?

`state/git.lock.d` sat orphaned from 10-Aug 21:34 until it was cleared by hand
two days later. A worker held it when the rate-limit abort batch tore the run
down, and because the directory was empty nothing could tell a live holder
from a corpse: every runner since waited the full ten minutes and gave up with
"Could not acquire git lock after 10m; skipping commit". Completed research
went uncommitted, and GXH.NZ and HGH.NZ sat in history as "missing
Dashboard.html" while both dashboards were on disk.

`mkdir` stays the atomic acquire in lib.sh -- stock macOS has no `flock(1)`,
and mkdir is atomic on every POSIX filesystem. What this adds is the owner
record that makes the lock recoverable, and the decision about whether a lock
may be reclaimed or released.

The rule throughout: never touch a lock unless it can be *proven* not to be
someone else's. A false positive deletes a sibling's live lock and lets two
commits interleave. Hence a lock owned by a running PID is untouchable, and
`release` only removes a lock whose recorded PID matches the caller's -- an
exit trap runs in every worker, including ones that never acquired anything.

Usage:
  git_lock.py --check LOCKDIR              # exit 0 = stale (reclaimable)
  git_lock.py --reclaim LOCKDIR            # remove it if stale
  git_lock.py --release LOCKDIR --pid PID  # remove it if PID owns it
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
OWNER_FILE = "owner"


def owner(lockdir: pathlib.Path | str) -> tuple[int, str] | None:
    """(pid, ticker) recorded inside the lock, or None if unreadable.

    None covers the orphan that caused all this: a lock directory with no
    owner file at all.
    """
    path = pathlib.Path(lockdir) / OWNER_FILE
    try:
        parts = path.read_text().split()
    except (OSError, UnicodeDecodeError):
        return None
    if not parts:
        return None
    try:
        pid = int(parts[0])
    except ValueError:
        return None
    return pid, (parts[1] if len(parts) > 1 else "")


def _alive(pid: int) -> bool:
    """Whether a process exists. Signal 0 checks without delivering."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Owned by another user: it exists, so treat the lock as live.
        return True
    except OSError:
        return False
    return True


def is_stale(lockdir: pathlib.Path | str) -> bool:
    """True when the lock can be reclaimed: no owner, or a dead one."""
    lockdir = pathlib.Path(lockdir)
    if not lockdir.is_dir():
        return False                 # nothing held; just try to acquire
    got = owner(lockdir)
    if got is None:
        # No usable owner record. Reclaimable by necessity -- otherwise the
        # 10-Aug orphan is a permanent deadlock needing a human.
        return True
    return not _alive(got[0])


def reclaim(lockdir: pathlib.Path | str) -> bool:
    """Remove the lock if it is stale. True when it was removed."""
    lockdir = pathlib.Path(lockdir)
    if not is_stale(lockdir):
        return False
    # rmtree, not rmdir: the lock holds an owner file, and may hold others.
    shutil.rmtree(lockdir, ignore_errors=True)
    return not lockdir.exists()


def release(lockdir: pathlib.Path | str, pid: int) -> bool:
    """Remove the lock only if `pid` owns it. True when it was removed.

    Deliberately stricter than reclaim(): this runs from an exit trap in every
    worker, so an ownerless lock is left alone rather than assumed to be ours.
    """
    lockdir = pathlib.Path(lockdir)
    if not lockdir.is_dir():
        return False
    got = owner(lockdir)
    if got is None or got[0] != pid:
        return False
    shutil.rmtree(lockdir, ignore_errors=True)
    return not lockdir.exists()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", metavar="LOCKDIR",
                      help="exit 0 if the lock is stale, 1 if it is live")
    mode.add_argument("--reclaim", metavar="LOCKDIR",
                      help="remove the lock if it is stale")
    mode.add_argument("--release", metavar="LOCKDIR",
                      help="remove the lock if --pid owns it")
    p.add_argument("--pid", type=int, default=None)
    args = p.parse_args()

    target = args.check or args.reclaim or args.release
    if not target:
        print("usage: git_lock.py (--check|--reclaim|--release) LOCKDIR",
              file=sys.stderr)
        return 2

    if args.check:
        got = owner(target)
        if got:
            state = "live" if _alive(got[0]) else "dead"
            print(f"pid {got[0]} ({got[1] or 'unknown'}) {state}")
        else:
            print("no owner recorded")
        return 0 if is_stale(target) else 1

    if args.reclaim:
        return 0 if reclaim(target) else 1

    if args.pid is None:
        print("--release needs --pid", file=sys.stderr)
        return 2
    return 0 if release(target, args.pid) else 1


if __name__ == "__main__":
    raise SystemExit(main())
