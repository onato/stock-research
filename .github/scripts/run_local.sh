#!/usr/bin/env bash
# Local screener runner -- the same queue/budget logic as the GitHub
# Actions workflow, but using the locally authenticated `claude` CLI.
#
# In use until the GitHub-hosted path is viable: the intellum.com org
# disables Claude subscription access for Claude Code, so the Max token is
# rejected there with 403 "oauth_org_not_allowed". Locally there is no such
# restriction, so this bills against the subscription rather than an API key.
#
# Usage:
#   .github/scripts/run_local.sh                 # next queued ticker
#   .github/scripts/run_local.sh SEK.NZ          # a specific ticker
#   .github/scripts/run_local.sh -n 5            # loop over 5 tickers
#   .github/scripts/run_local.sh --no-push       # commit but do not push
#   .github/scripts/run_local.sh --ignore-budget # do not consume a slot
#
# The weekend restriction is deliberately NOT enforced here -- that rule
# existed to bound unattended cloud spend. Running by hand is already
# bounded by you being present. The per-weekend budget still applies unless
# --ignore-budget is passed.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

COUNT=1
TICKER=""
PUSH=1
BUDGET_ARGS=""

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--count)      COUNT="$2"; shift 2 ;;
    --no-push)       PUSH=0; shift ;;
    --ignore-budget) BUDGET_ARGS="--ignore-budget"; shift ;;
    -h|--help)       sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)              echo "Unknown option: $1" >&2; exit 2 ;;
    *)               TICKER="$1"; shift ;;
  esac
done

command -v claude     >/dev/null || { echo "ERROR: claude CLI not found" >&2; exit 1; }
command -v pdftotext  >/dev/null || { echo "ERROR: pdftotext not found (brew install poppler)" >&2; exit 1; }

# Emulate $GITHUB_OUTPUT so the shared scripts work unchanged.
GITHUB_OUTPUT="$(mktemp)"; export GITHUB_OUTPUT

read_out() { grep "^$1=" "$GITHUB_OUTPUT" | tail -1 | cut -d= -f2-; }

for i in $(seq 1 "$COUNT"); do
  echo ""
  echo "=============================================="
  echo " Screener run $i of $COUNT"
  echo "=============================================="

  : > "$GITHUB_OUTPUT"

  # Weekend check is skipped locally; the budget still applies.
  if ! python3 .github/scripts/guard.py --max-runs 8 --ignore-weekend $BUDGET_ARGS; then
    echo "Guard failed unexpectedly." >&2
    exit 1
  fi
  if [ "$(read_out proceed)" != "true" ]; then
    echo "Stopping: weekend budget exhausted ($(read_out runs_used)/$(read_out max_runs))."
    echo "Pass --ignore-budget to override."
    break
  fi

  : > "$GITHUB_OUTPUT"
  python3 .github/scripts/select_ticker.py --override "$TICKER" >/dev/null || exit 1
  T="$(read_out ticker)"
  MODE="$(read_out mode)"

  if [ -z "$T" ]; then
    echo "Nothing to do: queue exhausted and no DCFs to refresh."
    break
  fi

  echo "Ticker: $T  (mode: $MODE)"
  echo ""

  # Uses the local CLI's own auth -- no API key involved.
  if ! claude --permission-mode bypassPermissions -p "/research-stock $T"; then
    echo ""
    echo "WARNING: research-stock exited non-zero for $T." >&2
    echo "Committing whatever it produced, if anything." >&2
  fi

  # PDFs are gitignored, so this stages only text/CSV/JSON/HTML output.
  git add -A -- "$T" index.html .github/state/budget.json 2>/dev/null

  if git diff --cached --quiet; then
    echo "No output produced for $T -- nothing to commit."
  else
    git commit -q -m "feat(screener): $MODE research for $T

Automated local run via .github/scripts/run_local.sh"
    echo "Committed $T."
    if [ "$PUSH" = "1" ]; then
      git pull -q --rebase --autostash && git push -q && echo "Pushed."
    fi
  fi

  # An explicit ticker is a one-shot; looping would redo the same name.
  [ -n "$TICKER" ] && break
done

rm -f "$GITHUB_OUTPUT"
echo ""
echo "Done."
