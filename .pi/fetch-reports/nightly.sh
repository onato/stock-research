#!/bin/bash
# Nightly gated report fetch over the international tickers (US bare symbols are
# excluded — EDGAR is handled deterministically by the main pipeline).
# Run by launchd: ~/Library/LaunchAgents/com.swilliams.fetch-reports.plist
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

if [ -z "$BATCH" ]; then
  # Default: one nightly pass — updates for all researched tickers + 3 seeds.
  SEEDS=($(python3 "$HERE/next_new.py" 3))
  echo "=== nightly fetch $(date '+%F %T') — ${#TICKERS[@]} updates + ${#SEEDS[@]} seeds (${SEEDS[*]:-none}) ==="
  "$HERE/run.sh" "${TICKERS[@]}" "${SEEDS[@]}"
  echo "=== done $(date '+%F %T') ==="
else
  # Continuous mode: `nightly.sh N` seeds the queue in batches of N until the
  # queue runs dry or the process is stopped (Ctrl-C, or: pkill -f nightly.sh).
  # caffeinate -s keeps the Mac awake (AC power) for as long as this runs.
  ATTEMPTED=""
  batch_no=0
  while :; do
    SEEDS=($(python3 "$HERE/next_new.py" "$BATCH" --exclude "${ATTEMPTED#,}"))
    [ ${#SEEDS[@]} -eq 0 ] && { echo "=== queue exhausted after $batch_no batches $(date '+%F %T') ==="; break; }
    batch_no=$((batch_no + 1))
    echo "=== batch $batch_no $(date '+%F %T') — seeding: ${SEEDS[*]} ==="
    /usr/bin/caffeinate -s "$HERE/run.sh" "${SEEDS[@]}"
    for t in "${SEEDS[@]}"; do ATTEMPTED="$ATTEMPTED,$t"; done
  done
fi
