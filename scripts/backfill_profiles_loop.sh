#!/usr/bin/env bash
# Chain profile-backfill batches until the queue is empty or the host pushes
# back.
#
# The single-batch script paces itself (6s jittered per request) and that
# pacing has held: 49 consecutive fetches with zero rate limits. So rather
# than one 150-batch a night for six nights, this keeps going -- at ~8s a
# ticker an overnight run clears the whole backlog.
#
# It stops the moment it is asked to. backfill_profiles.py exits 3 when it
# backs off after MAX_CONSECUTIVE_429 rate limits; this loop treats that as
# final and does not retry, because retrying a host that has just asked us to
# stop is how a soft limit becomes a hard block.
#
# Each batch commits its own work, so an interrupted loop leaves nothing
# loose and the next run resumes from the state file.
#
# Usage:
#   scripts/backfill_profiles_loop.sh                  # until done or limited
#   scripts/backfill_profiles_loop.sh --batch 50       # smaller batches
#   scripts/backfill_profiles_loop.sh --until 0630     # stop at a wall time
#   scripts/backfill_profiles_loop.sh --max-batches 4  # bounded
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

BATCH=100
UNTIL=""
MAX_BATCHES=0          # 0 = unbounded
PAUSE=30               # between batches; a breather the host never asked for

while [ $# -gt 0 ]; do
  case "$1" in
    --batch)       BATCH="$2"; shift 2 ;;
    --until)       UNTIL="$2"; shift 2 ;;
    --max-batches) MAX_BATCHES="$2"; shift 2 ;;
    --pause)       PAUSE="$2"; shift 2 ;;
    -h|--help)     sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)             echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

EXIT_RATE_LIMITED=3
n=0
START="$(date '+%F %T')"
echo "=== profile backfill loop started $START (batch $BATCH) ==="

while true; do
  # --until names a MORNING wall time, and an overnight run starts the evening
  # before, so a plain `now >= until` is true immediately at 23:05 and the loop
  # exits without doing anything. Only apply the stop once we are actually in
  # the small hours (i.e. now is earlier in the day than the deadline window).
  if [ -n "$UNTIL" ]; then
    now="$(date '+%H%M')"
    # 10#: strip the leading zero, or bash reads 0630 as octal.
    if [ "$((10#$now))" -ge "$((10#$UNTIL))" ] && [ "$((10#$now))" -lt 1200 ]; then
      echo "=== reached $UNTIL -- stopping ==="
      break
    fi
  fi
  if [ "$MAX_BATCHES" -gt 0 ] && [ "$n" -ge "$MAX_BATCHES" ]; then
    echo "=== $MAX_BATCHES batches done -- stopping ==="
    break
  fi

  # How much is left? When nothing is, we are finished.
  remaining="$(uv run python3 scripts/backfill_profiles.py --status 2>/dev/null \
                | awk '/remaining/ {for (i=1;i<=NF;i++) if ($i=="remaining") print $(i+1)}')"
  if [ -z "$remaining" ] || [ "$remaining" -eq 0 ] 2>/dev/null; then
    echo "=== queue exhausted after $n batch(es) ==="
    break
  fi

  n=$((n + 1))
  echo "--- batch $n ($remaining remaining) $(date '+%T') ---"
  uv run python3 scripts/backfill_profiles.py --limit "$BATCH" --commit
  rc=$?

  if [ "$rc" -eq "$EXIT_RATE_LIMITED" ]; then
    echo "=== rate limited -- stopping after $n batch(es); progress is saved ==="
    break
  fi
  if [ "$rc" -ne 0 ]; then
    echo "=== batch $n exited $rc -- stopping ==="
    break
  fi

  sleep "$PAUSE"
done

uv run python3 scripts/backfill_profiles.py --status
echo "=== loop finished $(date '+%F %T') (started $START) ==="
