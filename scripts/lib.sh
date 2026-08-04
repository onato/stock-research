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

  # Appended to the skill invocation. Batch runs are unattended, so the
  # model's usual finishing touches are wrong here: `open` pops a browser
  # window per ticker, and a long written summary just restates the
  # dashboard and JSON reports it already wrote to disk.
  local batch_note="Run non-interactively as part of an unattended batch. \
Do not open, preview, or launch any file (no \`open\`, no browser). \
When finished, reply with at most two sentences stating the ticker and \
the files written -- the dashboard and reports are the deliverable, so \
do not summarize their contents."

  # stream-json emits one event per line as work proceeds, so a ~40min run
  # is observable instead of silent. Without it, plain -p buffers everything
  # until completion, which is indistinguishable from a hang.
  if [ "$quiet" = "--quiet" ]; then
    claude --permission-mode bypassPermissions \
           --disallowed-tools "Bash(open *)" \
           --output-format stream-json --verbose \
           -p "/research-stock $ticker

$batch_note" > "$log" 2>&1
    rc=$?
  else
    set -o pipefail
    claude --permission-mode bypassPermissions \
           --disallowed-tools "Bash(open *)" \
           --output-format stream-json --verbose \
           -p "/research-stock $ticker

$batch_note" \
      | tee "$log" \
      | python3 "$REPO_ROOT/scripts/progress.py"
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

  git add -A -- "research/$ticker" index.html state/budget.json \
    state/scores evals 2>/dev/null

  # Screener outputs are staged separately: a missing pathspec aborts the
  # whole `git add`, and these only exist once `make screen` has run.
  git add -A -- state/last_screen.json state/companies.json 2>/dev/null || true

  if git diff --cached --quiet; then
    echo "[$ticker] No output produced -- nothing to commit."
    return 0
  fi

  git commit -q -m "feat: $mode research for $ticker

Automated local run via scripts/"
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
  local lockdir="$REPO_ROOT/state/git.lock.d"
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
# wait_for_rate_limit <log>
#
# Scans a run's transcript for a rate_limit_event whose status is not
# "allowed". If found, sleeps until the reported reset time and returns 1 so
# the caller can retry the ticker; returns 0 when no limit was hit.
#
# During a long unattended drain this is the difference between surviving a
# limit window and burning the rest of the queue on requests that cannot
# succeed. resetsAt is a unix epoch (verified against a live event).
#
# The sleep is clamped: never negative, and never longer than MAX_RL_SLEEP
# (default 2h) so a bogus timestamp cannot park the loop indefinitely.
# ---------------------------------------------------------------------------
wait_for_rate_limit() {
  local log="$1"
  local secs

  [ -f "$log" ] || return 0

  secs=$(python3 - "$log" "${MAX_RL_SLEEP:-7200}" <<'PY'
import json, sys, time

log, cap = sys.argv[1], int(sys.argv[2])
reset = None
for line in open(log, errors="replace"):
    line = line.strip()
    if not line.startswith("{") or "rate_limit_event" not in line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue
    if ev.get("type") != "rate_limit_event":
        continue
    info = ev.get("rate_limit_info") or {}
    if info.get("status") in (None, "allowed"):
        continue
    r = info.get("resetsAt")
    if isinstance(r, (int, float)):
        # Keep the furthest reset seen; a run may log several events.
        reset = r if reset is None else max(reset, r)

if reset is None:
    print(0)
else:
    # +30s of slack: waking exactly at the boundary tends to get rejected.
    print(max(0, min(cap, int(reset - time.time()) + 30)))
PY
  ) || return 0

  [ "${secs:-0}" -gt 0 ] 2>/dev/null || return 0

  printf 'RATE LIMIT hit -- sleeping %dm%02ds until reset (%s)\n' \
    $((secs / 60)) $((secs % 60)) \
    "$(date -v"+${secs}S" +%H:%M 2>/dev/null || date -d "+${secs} seconds" +%H:%M 2>/dev/null || echo '?')" >&2
  sleep "$secs"
  return 1
}

# ---------------------------------------------------------------------------
# require_tools -- fail fast if the runtime dependencies are missing.
# ---------------------------------------------------------------------------
require_tools() {
  command -v claude    >/dev/null || { echo "ERROR: claude CLI not found" >&2; exit 1; }
  command -v pdftotext >/dev/null || { echo "ERROR: pdftotext not found (brew install poppler)" >&2; exit 1; }
}
