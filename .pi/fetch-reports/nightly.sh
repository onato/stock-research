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
trap 'echo "interrupted — stopping"; exit 130' INT TERM
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
START_TS="$(date +%FT%T)"

seed_batch() {  # $1 = batch size; returns 1 when the queue is exhausted
  local seeds=($(uv run --project "$REPO" python3 "$HERE/next_new.py" "$1" --exclude "${ATTEMPTED#,}"))
  [ ${#seeds[@]} -eq 0 ] && return 1
  echo "=== seeding $(date '+%F %T') — ${seeds[*]} ==="
  /usr/bin/caffeinate -s "$HERE/run.sh" "${seeds[@]}"
  local t; for t in "${seeds[@]}"; do ATTEMPTED="$ATTEMPTED,$t"; done
  return 0
}

if [ -z "$BATCH" ]; then
  # Default nightly pass: updates first, then seed until 05:45 local time.
  # The deadline applies to the WHOLE night (run.sh checks it between tickers),
  # and tickers attempted in the last 3 days are skipped — a corpus this size
  # only changes twice a year per company, so nightly full sweeps are waste.
  if [ "$(date '+%H%M')" -lt 0545 ]; then export FETCH_DEADLINE=0545; fi
  TICKERS=($(printf '%s\n' "${TICKERS[@]}" | uv run --project "$REPO" python3 -c "
import sys, datetime
from pathlib import Path
cutoff = (datetime.datetime.now() - datetime.timedelta(days=3)).isoformat()
last = {}
tsv = Path('$HERE/logs/attempts.tsv')
if tsv.exists():
    for line in tsv.read_text().splitlines():
        t, _, ts = line.partition('\t')
        if ts > last.get(t, ''): last[t] = ts
for t in sys.stdin.read().split():
    if last.get(t, '') < cutoff: print(t)
"))
  echo "=== nightly fetch $(date '+%F %T') — ${#TICKERS[@]} due updates (deadline ${FETCH_DEADLINE:-none}) ==="
  if [ ${#TICKERS[@]} -gt 0 ]; then
    /usr/bin/caffeinate -s "$HERE/run.sh" ${TICKERS[@]+"${TICKERS[@]}"}
  fi
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

# Send a Telegram summary if credentials are configured (telegram.env is
# gitignored; create it with TELEGRAM_BOT_TOKEN=... and TELEGRAM_CHAT_ID=...).
if [ -f "$HERE/telegram.env" ]; then
  . "$HERE/telegram.env"
  SUMMARY="$(uv run --project "$REPO" python3 "$HERE/summarize.py" --since "$START_TS")"
  MODE_LINE="nightly pass"; [ -n "$BATCH" ] && MODE_LINE="continuous run (batch $BATCH)"
  curl -sf --max-time 30 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode chat_id="${TELEGRAM_CHAT_ID}" \
    --data-urlencode text="🌙 Report fetch — $MODE_LINE
$START_TS → $(date +%T)

$SUMMARY" > /dev/null && echo "telegram summary sent" || echo "telegram send FAILED"
fi
