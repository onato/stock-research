#!/usr/bin/env bash
# Research and commit a single ticker. This is the unit GNU parallel calls.
#
# The split matters: parallel owns *scheduling* (how many run at once, which
# starts next), this script owns the *work* for one ticker, and lib.sh owns
# the steps. Serial logic stays serial; nothing about running N at a time
# leaks into the research or commit sequence.
#
# Usage: research_one.sh TICKER
#
# Honors from the environment (run_loop.sh sets these):
#   PUSH=0|1        push after committing (default 1)
#   LOG_DIR         where transcripts go (default .github/state/logs)
#   RL_RETRIES      retries after a rate-limit pause (default 1)
#
# Exit status is the research exit status, so parallel's --joblog records
# which tickers actually failed.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

TICKER="${1:-}"
[ -z "$TICKER" ] && { echo "usage: research_one.sh TICKER" >&2; exit 2; }

: "${PUSH:=1}"
: "${LOG_DIR:=.github/state/logs}"
: "${RL_RETRIES:=1}"
export PUSH LOG_DIR

. "$REPO_ROOT/.github/scripts/lib.sh"

attempt=0
while : ; do
  research_ticker "$TICKER" --quiet
  rc=$?

  # A rate-limit rejection is not a failure of this ticker -- it means the
  # request never got a fair chance. Sleep until the window resets and try
  # once more rather than recording a spurious failure.
  if wait_for_rate_limit "$LOG_DIR/$TICKER.log"; then
    break                       # no limit hit; keep whatever rc we got
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -gt "$RL_RETRIES" ]; then
    echo "[$TICKER] still rate-limited after $RL_RETRIES retry(ies); giving up." >&2
    break
  fi
  echo "[$TICKER] retrying after rate-limit reset (attempt $((attempt + 1)))." >&2
done

# Score the output and snapshot the prediction before committing, so the
# scorecard and ledger row land in the same commit as the artifacts they
# describe. Eval failures never fail the run -- they are a report, not a gate.
python3 "$REPO_ROOT/.github/scripts/run_evals.py" "$TICKER" || true
python3 "$REPO_ROOT/.github/scripts/ledger.py" append "$TICKER" || true

# Commit whatever was produced, even on a non-zero exit -- a partial run
# still leaves useful extracted text and metrics on disk.
with_git_lock "$TICKER" commit_ticker "$TICKER"

exit "$rc"
