#!/bin/bash
# Nightly gated report fetch over the international tickers (US bare symbols are
# excluded — EDGAR is handled deterministically by the main pipeline).
# Run by launchd: ~/Library/LaunchAgents/com.swilliams.fetch-reports.plist
set -u
export PATH="$HOME/.local/share/mise/shims:$HOME/.local/share/mise/installs/node/23.0.0/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

TICKERS=()
for d in "$REPO"/research/*/; do
  t="$(basename "$d")"
  if [[ "$t" =~ ^[A-Z0-9]+\.(NZ|L|HK|AX)$ ]]; then
    TICKERS+=("$t")
  fi
done

echo "=== nightly fetch $(date '+%F %T') — ${#TICKERS[@]} tickers ==="
"$HERE/run.sh" "${TICKERS[@]}"
echo "=== done $(date '+%F %T') ==="
