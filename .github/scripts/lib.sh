#!/usr/bin/env bash
# Shared helpers for the local screener runners.
#
# run_local.sh and run_parallel.sh differ in concurrency model -- sequential
# with a per-run budget check vs. reserve-a-batch-then-fan-out -- but the
# work each ticker needs is identical. That part lives here so a fix to the
# research invocation or the commit sequence lands in both.
#
# Source it, don't execute it:
#   . "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
#
# Expects the caller to have set: REPO_ROOT, LOG_DIR, PUSH.

# ---------------------------------------------------------------------------
# research_ticker <ticker> [--quiet]
#
# Runs the research-stock skill for one ticker. The full JSON event stream is
# always written to $LOG_DIR/<ticker>.log; by default a readable summary is
# also printed live via progress.py.
#
# --quiet suppresses the live summary (for parallel runs, where several
# interleaved streams are unreadable). Returns the CLI's exit status.
# ---------------------------------------------------------------------------
research_ticker() {
  local ticker="$1" quiet="${2:-}"
  local log="$LOG_DIR/$ticker.log"
  local start rc elapsed

  mkdir -p "$LOG_DIR"
  start=$(date +%s)

  # stream-json emits one event per line as work proceeds, so a ~40min run
  # is observable instead of silent. Without it, plain -p buffers everything
  # until completion, which is indistinguishable from a hang.
  if [ "$quiet" = "--quiet" ]; then
    claude --permission-mode bypassPermissions \
           --output-format stream-json --verbose \
           -p "/research-stock $ticker" > "$log" 2>&1
    rc=$?
  else
    set -o pipefail
    claude --permission-mode bypassPermissions \
           --output-format stream-json --verbose \
           -p "/research-stock $ticker" \
      | tee "$log" \
      | python3 "$REPO_ROOT/.github/scripts/progress.py"
    rc=$?
    set +o pipefail
  fi

  elapsed=$(( $(date +%s) - start ))
  printf '[%s] finished in %dm%02ds (exit %d)\n' \
    "$ticker" $((elapsed / 60)) $((elapsed % 60)) "$rc"

  [ "$rc" != "0" ] && echo "[$ticker] non-zero exit -- see $log" >&2
  return "$rc"
}

# ---------------------------------------------------------------------------
# commit_ticker <ticker> [mode]
#
# Stages and commits one ticker's output. PDFs are gitignored, so this picks
# up only extracted text, CSV, JSON, and HTML. Rebases before pushing so a
# concurrent commit doesn't cause a rejected push.
#
# Callers running concurrently must hold the git lock (see with_git_lock).
# ---------------------------------------------------------------------------
commit_ticker() {
  local ticker="$1" mode="${2:-new}"

  git add -A -- "$ticker" index.html .github/state/budget.json 2>/dev/null

  if git diff --cached --quiet; then
    echo "[$ticker] No output produced -- nothing to commit."
    return 0
  fi

  git commit -q -m "feat(screener): $mode research for $ticker

Automated local run via .github/scripts/"
  echo "[$ticker] Committed."

  if [ "$PUSH" = "1" ]; then
    git pull -q --rebase --autostash && git push -q && echo "[$ticker] Pushed."
  fi
}

# ---------------------------------------------------------------------------
# with_git_lock <ticker> <command...>
#
# Serializes git access across concurrent runners. Uses an mkdir spinlock
# because stock macOS has no flock(1); mkdir is atomic on every POSIX
# filesystem. A no-op for sequential callers (the lock is never contended).
# ---------------------------------------------------------------------------
with_git_lock() {
  local ticker="$1"; shift
  local lockdir="$REPO_ROOT/.github/state/git.lock.d"
  local waited=0

  while ! mkdir "$lockdir" 2>/dev/null; do
    sleep 2
    waited=$((waited + 2))
    if [ "$waited" -gt 600 ]; then
      echo "[$ticker] Could not acquire git lock after 10m; skipping commit." >&2
      return 1
    fi
  done

  "$@"
  local rc=$?
  rmdir "$lockdir" 2>/dev/null
  return "$rc"
}

# ---------------------------------------------------------------------------
# require_tools -- fail fast if the runtime dependencies are missing.
# ---------------------------------------------------------------------------
require_tools() {
  command -v claude    >/dev/null || { echo "ERROR: claude CLI not found" >&2; exit 1; }
  command -v pdftotext >/dev/null || { echo "ERROR: pdftotext not found (brew install poppler)" >&2; exit 1; }
}
