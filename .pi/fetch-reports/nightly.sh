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
# A bare mkdir lock cannot tell a live run from a corpse. The 2026-08-26 pass
# died mid-fetch (SIGKILL / reboot skips the EXIT trap) and the leftover
# directory silently disabled the nightly for SIX consecutive nights -- the log
# just repeats "another nightly run is active". git_lock.py records the owning
# PID and reclaims the lock only when that process is genuinely gone, which is
# the same fix scripts/lib.sh already applies to the git lock.
if ! mkdir "$LOCK" 2>/dev/null; then
  if uv run --project "$REPO" python3 "$REPO/scripts/git_lock.py" \
       --reclaim "$LOCK" >/dev/null 2>&1 && mkdir "$LOCK" 2>/dev/null; then
    echo "reclaimed an abandoned nightly lock ($LOCK)"
  else
    holder="$(uv run --project "$REPO" python3 "$REPO/scripts/git_lock.py" \
                --check "$LOCK" 2>/dev/null)"
    echo "another nightly run is active ($LOCK, held by ${holder:-?}) — exiting"
    exit 0
  fi
fi
printf '%s %s\n' "$$" nightly > "$LOCK/owner" 2>/dev/null || true
# Release on signals too, not just a clean EXIT.
trap 'rm -rf "$LOCK"' EXIT
trap 'rm -rf "$LOCK"; exit 143' INT TERM

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

# Fill in company names and business descriptions for queued tickers that are
# still a bare symbol, so the ethical screen can judge one BEFORE a research
# run is spent on it. Deliberately last: it is best-effort enrichment, and a
# rate limit must not cost us the night's fetch work.
#
# The script paces itself (jittered delay, escalating backoff on a 429) and
# commits its own output, so there is nothing to clean up if it is cut short.
# Once the queue is drained this is a few seconds a night.
# The backfill gets its own small budget AFTER the fetch, not a share of the
# fetch's window. The fetch routinely runs to its 05:45 deadline -- 4 of the
# last 8 nights finished at or past it -- so gating the backfill on that
# deadline would have skipped it on half of them, which is exactly the
# starvation it was added to avoid.
#
# A batch of 150 at ~8s each is ~20 min, so it is bounded regardless; the
# ceiling below is a backstop for a night that started very late. Best-effort:
# a failure here must never cost the night's fetch work.
BACKFILL_UNTIL="${BACKFILL_UNTIL:-0630}"
echo "=== profile backfill $(date '+%F %T') ==="
if [ "$(date '+%H%M')" -ge "$BACKFILL_UNTIL" ]; then
  echo "past ${BACKFILL_UNTIL} — skipping profile backfill tonight"
else
  uv run --project "$REPO" python3 "$REPO/scripts/backfill_profiles.py" \
    --limit "${PROFILE_BATCH:-150}" --commit || echo "profile backfill failed (non-fatal)"
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
