#!/bin/bash
# Poll for the Adyen H1-2026 shareholder letter (due 13-Aug-2026 07:00 CEST) and
# download it the moment it lands, independently of any Claude session.
#
# Driven by ~/Library/LaunchAgents/com.swilliams.adyen-h1-watch.plist, every 10 min.
# Self-disarms once the letter is captured so it cannot loop forever.
set -uo pipefail

REPO="/Users/swilliams/Stocks/Research"
LOG="$REPO/state/adyen_h1_watch.log"
STAMP="$REPO/state/.adyen_h1_captured"
PDF="$REPO/research/ADYEY/PDFs/ADYEY_Letter_H1-2026.pdf"
PLIST="$HOME/Library/LaunchAgents/com.swilliams.adyen-h1-watch.plist"

cd "$REPO" || exit 1
say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# Already captured -> unload self and stop.
if [ -f "$STAMP" ]; then
  say "already captured; unloading watcher"
  launchctl unload "$PLIST" 2>/dev/null
  exit 0
fi

if ! python3 scripts/check_adyen_h1.py >> "$LOG" 2>&1; then
  exit 0  # not published yet; stay armed
fi

say "LETTER IS LIVE - downloading"
if ! python3 scripts/check_adyen_h1.py --download >> "$LOG" 2>&1; then
  say "download FAILED - staying armed for retry"
  exit 1
fi

[ -s "$PDF" ] || { say "PDF missing/empty - staying armed"; exit 1; }

# Extract to both folders: ADYEY holds the corpus of record, ADYEN.AS mirrors it.
# This MUST succeed - if the H1-2026 text is missing, Claude would silently read the
# H1-2025 file still sitting in Extracted/ and confidently grade last year's numbers.
mkdir -p research/ADYEY/Extracted research/ADYEN.AS/Extracted
SRC="research/ADYEY/Extracted/ADYEY_Letter_H1-2026.txt"
DST="research/ADYEN.AS/Extracted/ADYEN.AS_Letter_H1-2026.txt"
if ! pdftotext -layout "$PDF" "$SRC" 2>> "$LOG" || [ ! -s "$SRC" ]; then
  say "EXTRACTION FAILED - staying armed, not handing stale data to Claude"
  rm -f "$SRC" "$DST"
  [ -x "$HOME/.claude/skills/telegram/send.sh" ] && \
    "$HOME/.claude/skills/telegram/send.sh" "Adyen H1-2026 PDF downloaded but text extraction failed. Open Claude and read it manually." >> "$LOG" 2>&1
  exit 1
fi
cp "$SRC" "$DST"
say "extracted to ADYEY + ADYEN.AS ($(wc -l < "$SRC") lines)"

touch "$STAMP"
say "CAPTURED $(wc -c < "$PDF") bytes - handing to Claude for the verdict"

SEND="$HOME/.claude/skills/telegram/send.sh"

# Hand the letter to Claude headlessly. Claude reads the pre-committed criteria,
# grades the print, and sends the Telegram itself via the telegram skill.
export PATH="$HOME/.local/bin:/usr/bin:/bin:/usr/local/bin"
if claude -p "$(cat "$REPO/state/adyen_h1_verdict_prompt.md")" \
      --permission-mode acceptEdits \
      --add-dir "$REPO" >> "$LOG" 2>&1; then
  say "claude verdict completed"
else
  say "claude verdict FAILED - falling back to a raw alert"
  [ -x "$SEND" ] && "$SEND" "Adyen H1-2026 letter downloaded, but the automated verdict failed. Open Claude and ask for the H1 read." >> "$LOG" 2>&1
fi

launchctl unload "$PLIST" 2>/dev/null
say "watcher disarmed"
