#!/bin/bash
# Nightly gated report fetch over the international tickers (US bare symbols are
# excluded — EDGAR is handled deterministically by the main pipeline).
# Run by launchd at 02:00: ~/Library/LaunchAgents/com.swilliams.fetch-reports.plist
#
# Modes:
#   nightly.sh      update pass for all researched tickers, then seed the queue
#                   in batches of 3 until 05:45 (at least one batch regardless)
#   nightly.sh N    continuous: seed batches of N until stopped or queue dry
set -u
export PATH="$HOME/.local/share/mise/shims:$HOME/.local/share/mise/installs/node/23.0.0/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

LOCK="$HERE/.nightly.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "another nightly run is active ($LOCK exists) — exiting"
  exit 0
fi
trap 'rmdir "$LOCK"' EXIT

TICKERS=()
for d in "$REPO"/research/*/; do
  t="$(basename "$d")"
  if [[ "$t" =~ ^[A-Z0-9]+\.(NZ|L|HK|AX)$ ]]; then
    TICKERS+=("$t")
  fi
done

BATCH="${1:-}"
ATTEMPTED=""

seed_batch() {  # $1 = batch size; returns 1 when the queue is exhausted
  local seeds=($(python3 "$HERE/next_new.py" "$1" --exclude "${ATTEMPTED#,}"))
  [ ${#seeds[@]} -eq 0 ] && return 1
  echo "=== seeding $(date '+%F %T') — ${seeds[*]} ==="
  /usr/bin/caffeinate -s "$HERE/run.sh" "${seeds[@]}"
  local t; for t in "${seeds[@]}"; do ATTEMPTED="$ATTEMPTED,$t"; done
  return 0
}

if [ -z "$BATCH" ]; then
  # Default nightly pass: updates first, then seed until 05:45 local time
  # (started at 02:00 that means roughly four hours of work, ending near 06:00).
  echo "=== nightly fetch $(date '+%F %T') — ${#TICKERS[@]} updates ==="
  /usr/bin/caffeinate -s "$HERE/run.sh" "${TICKERS[@]}"
  seed_batch 3 || echo "queue exhausted"
  while [ "$(date '+%H%M')" -lt 0545 ]; do
    seed_batch 3 || { echo "queue exhausted"; break; }
  done
  echo "=== done $(date '+%F %T') ==="
else
  # Continuous mode: batches of N until stopped (Ctrl-C / pkill -f nightly.sh)
  # or the queue runs dry.
  n=0
  while seed_batch "$BATCH"; do n=$((n + 1)); done
  echo "=== queue exhausted after $n batches $(date '+%F %T') ==="
fi
