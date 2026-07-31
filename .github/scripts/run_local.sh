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
#   .github/scripts/run_local.sh --open          # open the dashboard when done
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
OPEN_DASH=0
BUDGET_ARGS=""

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--count)      COUNT="$2"; shift 2 ;;
    --no-push)       PUSH=0; shift ;;
    --open)          OPEN_DASH=1; shift ;;
    --ignore-budget) BUDGET_ARGS="--ignore-budget"; shift ;;
    -h|--help)       sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)              echo "Unknown option: $1" >&2; exit 2 ;;
    *)               TICKER="$1"; shift ;;
  esac
done

LOG_DIR=".github/state/logs"
. "$REPO_ROOT/.github/scripts/lib.sh"
require_tools

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

  # Live progress; commits whatever was produced even on a non-zero exit.
  research_ticker "$T" || echo "Committing whatever it produced, if anything." >&2
  commit_ticker "$T" "$MODE"

  # Opening is the caller's call, not the model's -- an unattended batch has
  # nobody to show it to. `open` is blocked inside the run (see lib.sh).
  if [ "$OPEN_DASH" = "1" ] && [ -f "$T/Reports/${T}_Dashboard.html" ]; then
    open "$T/Reports/${T}_Dashboard.html"
  fi

  # An explicit ticker is a one-shot; looping would redo the same name.
  [ -n "$TICKER" ] && break
done

rm -f "$GITHUB_OUTPUT"
echo ""
echo "Done."
