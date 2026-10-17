#!/bin/bash
#
# Weekly refresh of the quality-growth list, driven by
# ~/Library/LaunchAgents/com.swilliams.quality-growth.plist.
#
#   1. refresh prices (free, no model) so upside reflects the market
#   2. rebuild the warehouse and the deterministic list
#   3. if any candidate needs review, run the quality-growth-analyst agent
#      (sonnet) over just those tickers -- bounded cost, usually a handful
#   4. rebuild the list so the new notes and exclusions land, and commit
#      only the quality-growth files
#
# The portfolio tracker reads state/quality_growth.json on its own daily build.
#
# launchd gives a job a bare PATH with no shell profile, so it is set here.
set -uo pipefail
export PATH="$HOME/.local/share/mise/shims:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$REPO_ROOT/state/logs"
PUSH=""
export REPO_ROOT LOG_DIR PUSH
cd "$REPO_ROOT" || exit 1
mkdir -p "$LOG_DIR"
# shellcheck source=scripts/lib.sh
source "$REPO_ROOT/scripts/lib.sh"

echo "=== $(date '+%F %T') quality-growth refresh ==="

# Network can lag a wake-from-sleep launch.
for _ in $(seq 1 30); do
  /sbin/route -n get default >/dev/null 2>&1 && break
  sleep 10
done

make --no-print-directory update-prices DAILY=1 || echo "price refresh failed; continuing on stored prices"
make --no-print-directory quality-growth APPLY=1 >/dev/null || { echo "quality-growth build failed"; exit 1; }

pending="$(python3 -c 'import json;print(" ".join(json.load(open("state/quality_growth.json"))["needs_review"]))')"
if [ -n "$pending" ]; then
  echo "reviewing: $pending"
  export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
  claude --permission-mode bypassPermissions \
         --model "${QG_MODEL:-claude-sonnet-5}" \
         --disallowed-tools "Bash(open *)" "Bash(git *)" \
         -p "Use the quality-growth-analyst agent to review the tickers in state/quality_growth.json's needs_review list ($pending). Today is $(date +%F)." \
    > "$LOG_DIR/quality_growth_agent.log" 2>&1 \
    || echo "analyst agent exited non-zero; see state/logs/quality_growth_agent.log"
  make --no-print-directory quality-growth APPLY=1 >/dev/null || exit 1
else
  echo "no candidates need review"
fi

commit_list() {
  git add -- state/quality_growth.json state/quality_growth_notes
  # Pathspec-limited: commits only these files even if another runner has
  # something else staged.
  git commit -q -m "chore: refresh quality growth list" \
    -- state/quality_growth.json state/quality_growth_notes \
    || echo "nothing to commit"
}
# QG_NO_COMMIT=1 for a dry run: everything but the commit.
if [ -n "${QG_NO_COMMIT:-}" ]; then
  echo "QG_NO_COMMIT set; not committing"
else
  with_git_lock quality-growth commit_list
fi

echo "=== $(date '+%F %T') done ==="
