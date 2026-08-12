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
mkdir -p research/ADYEY/Extracted research/ADYEN.AS/Extracted
pdftotext -layout "$PDF" research/ADYEY/Extracted/ADYEY_Letter_H1-2026.txt 2>> "$LOG" \
  && cp research/ADYEY/Extracted/ADYEY_Letter_H1-2026.txt \
        research/ADYEN.AS/Extracted/ADYEN.AS_Letter_H1-2026.txt \
  && say "extracted to ADYEY + ADYEN.AS"

touch "$STAMP"
say "CAPTURED. $(wc -c < "$PDF") bytes. Ask Claude for the H1 verdict."

# Fire the Telegram heads-up via the skill's own sender (reads the macOS Keychain).
SEND="$HOME/.claude/skills/telegram/send.sh"
if [ -x "$SEND" ]; then
  if "$SEND" "Adyen H1-2026 letter is out and downloaded. Ask Claude for the verdict vs your workbook (weighted EUR1118 vs EUR933 price)." >> "$LOG" 2>&1; then
    say "telegram sent"
  else
    say "telegram FAILED (keychain locked from LaunchAgent?) - PDF is still captured"
  fi
else
  say "telegram sender not found - skipped"
fi

launchctl unload "$PLIST" 2>/dev/null
say "watcher disarmed"
