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
# Runs the skill this ticker needs -- /research-stock for a tier-3 ticker
# (new filings, or never researched), the cheaper /refresh-stock for a tier-2
# one (stale by date only), and nothing at all for tiers 0-1. See
# refresh_route.py. The full JSON event stream is
# always written to $LOG_DIR/<ticker>.log; by default a readable summary is
# also printed live via progress.py.
#
# --quiet suppresses the live summary (for parallel runs, where several
# interleaved streams are unreadable). Returns the CLI's exit status.
#
# Models are tiered by task difficulty. The orchestrator runs on
# BATCH_MODEL (default claude-opus-5) rather than inheriting the user's
# interactive default (Fable, at 2x Opus pricing); the per-stage agents set
# their own tier in .claude/agents/*.md frontmatter -- fable for the
# valuation (dcf-analyst), opus for adjudication (financial-parser),
# sonnet/haiku below that.
# ---------------------------------------------------------------------------
research_ticker() {
  local ticker="$1" quiet="${2:-}"
  local log="$LOG_DIR/$ticker.log"
  local stream="$LOG_DIR/$ticker.stream"
  local start rc elapsed skill_prompt

  mkdir -p "$LOG_DIR"

  # Which skill this ticker actually needs. A ticker that is stale only by
  # date -- its filings already parsed into the CSV -- does not need the
  # download/extract/parse half of the pipeline, and re-running it re-derives
  # numbers that cannot have moved. refresh_route returns the empty string
  # when no model should run at all (tier 0/1).
  #
  # FORCE=1 pins this to the full re-research regardless of tier.
  #
  # A routing *failure* and a legitimate "no work" both produce no usable
  # prompt, and they must not be confused: skipping a ticker because the
  # router crashed would silently drop it from the run. So the exit status
  # decides, and only a clean exit is allowed to mean "skip".
  if skill_prompt=$(uv run --project "$REPO_ROOT" python3 \
       "$REPO_ROOT/scripts/refresh_route.py" --ticker "$ticker" --prompt \
       ${FORCE:+--force} 2>/dev/null); then
    if [ -z "$skill_prompt" ]; then
      echo "[$ticker] nothing to do (tier 0/1) -- skipping the model."
      return 0
    fi
  else
    echo "[$ticker] refresh_route failed -- falling back to full research." >&2
    skill_prompt="/research-stock $ticker"
  fi
  echo "[$ticker] $skill_prompt"
  # Truncated per run so a pane tailing it shows this attempt, not the last
  # one's output followed by this one's.
  : > "$stream"
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

  # Backstop for the skill's "never end your turn with work still running"
  # rule: if the model backgrounds a subagent anyway, wait for it instead of
  # letting the harness terminate it at the default 600s ceiling — that
  # killed APL.NZ's financial-parser mid-adjudication (2026-08-04). The
  # run's own wall-clock timeout still bounds the wait.
  export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0

  # stream-json emits one event per line as work proceeds, so a ~40min run
  # is observable instead of silent. Without it, plain -p buffers everything
  # until completion, which is indistinguishable from a hang.
  if [ "$quiet" = "--quiet" ]; then
    # Parallel runs still need to show movement: sending the stream only to
    # the log left ~40 minutes per ticker with nothing on the terminal, which
    # is indistinguishable from a hang. --tools-only keeps it to one short
    # line per tool call, --label attributes it, and --heartbeat reports the
    # long silences (a subagent can hold a single call for many minutes).
    # The rendered trace goes two places. `$stream` is what watch_run.sh's
    # per-ticker tmux pane tails, so it carries no [TICKER] prefix -- the pane
    # border already names it, and the prefix costs ~10 columns of command
    # text. stdout keeps the prefix, because without tmux several tickers
    # share one terminal and the tag is the only thing telling them apart.
    set -o pipefail
    claude --permission-mode bypassPermissions \
           --model "${BATCH_MODEL:-claude-opus-5}" \
           --disallowed-tools "Bash(open *)" \
           --output-format stream-json --verbose \
           -p "$skill_prompt

$batch_note" 2>&1 \
      | tee "$log" \
      | uv run --project "$REPO_ROOT" python3 "$REPO_ROOT/scripts/progress.py" \
          --tools-only --heartbeat "${HEARTBEAT_SECS:-120}" \
      | tee "$stream" \
      | sed "s/^/[$ticker] /"
    rc=$?
    set +o pipefail
  else
    set -o pipefail
    claude --permission-mode bypassPermissions \
           --model "${BATCH_MODEL:-claude-opus-5}" \
           --disallowed-tools "Bash(open *)" \
           --output-format stream-json --verbose \
           -p "$skill_prompt

$batch_note" \
      | tee "$log" \
      | uv run --project "$REPO_ROOT" python3 "$REPO_ROOT/scripts/progress.py"
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

  # Partial output is still worth committing (extracted text is the durable
  # copy; PDFs are gitignored) but must not masquerade as finished research:
  # APL.NZ/AOF.NZ landed as "feat: new research" with no DCF or dashboard.
  # research_one.sh separately fails the run so the joblog records it.
  local missing=""
  for want in Metrics.csv DCF.json Dashboard.html; do
    [ -s "research/$ticker/Reports/${ticker}_$want" ] || missing="$missing $want"
  done

  if [ -n "$missing" ]; then
    git commit -q -m "wip: partial research for $ticker -- missing$missing

Automated local run via scripts/; incomplete, needs a re-run."
    echo "[$ticker] Committed PARTIAL output (missing$missing)."
  else
    git commit -q -m "feat: $mode research for $ticker

Automated local run via scripts/"
    echo "[$ticker] Committed."
  fi

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
    # A held lock might be an orphan. state/git.lock.d sat abandoned for two
    # days after the rate-limit abort batch killed a worker mid-commit, and
    # because the directory was empty nothing could tell a live holder from a
    # corpse -- every runner since waited the full 10m and skipped its commit.
    # git_lock.py --reclaim removes it only when the recorded owner is gone.
    if uv run --project "$REPO_ROOT" python3 "$REPO_ROOT/scripts/git_lock.py" \
         --reclaim "$lockdir" >/dev/null 2>&1; then
      echo "[$ticker] reclaimed an abandoned git lock." >&2
      continue                      # retry the mkdir straight away
    fi
    sleep 2
    waited=$((waited + 2))
    if [ "$waited" -gt 600 ]; then
      local held
      held="$(uv run --project "$REPO_ROOT" python3 \
                "$REPO_ROOT/scripts/git_lock.py" --check "$lockdir" 2>/dev/null)"
      echo "[$ticker] Could not acquire git lock after 10m (held by ${held:-?});" \
           "skipping commit." >&2
      return 1
    fi
  done

  # Record the holder so a waiter can tell this lock from an orphan, and so
  # the trap below only ever releases our own.
  printf '%s %s\n' "$$" "$ticker" > "$lockdir/owner" 2>/dev/null || true

  # Release on a signal too: without this, any kill during the critical
  # section leaks the lock permanently, which is how the two-day orphan
  # happened. --release is a no-op unless the recorded PID is ours, so a
  # dying worker cannot unlock a sibling's commit.
  local release_cmd="uv run --project '$REPO_ROOT' python3 \
'$REPO_ROOT/scripts/git_lock.py' --release '$lockdir' --pid $$ >/dev/null 2>&1"
  # shellcheck disable=SC2064
  # Expanding now is deliberate: the handler must capture this lockdir and PID
  # while they are in scope, not resolve locals that are gone by signal time.
  trap "$release_cmd; exit 143" INT TERM

  # Run the command in the BACKGROUND and wait on it. Bash defers a trap while
  # the shell blocks on a *foreground* child, so a SIGTERM mid-commit never ran
  # the handler and the lock leaked -- measured, and exactly how the two-day
  # orphan was created. Waiting on a background job keeps the shell
  # interruptible, so the trap fires and the lock is released.
  "$@" &
  local child=$!
  wait "$child"
  local rc=$?

  trap - INT TERM
  uv run --project "$REPO_ROOT" python3 "$REPO_ROOT/scripts/git_lock.py" \
    --release "$lockdir" --pid $$ >/dev/null 2>&1
  return "$rc"
}

# ---------------------------------------------------------------------------
# wait_for_rate_limit <log>
#
# Sleeps until a rejected request's window resets, then returns 1 so the
# caller can retry the ticker; returns 0 when nothing was blocked.
#
# During a long unattended drain this is the difference between surviving a
# limit window and burning the rest of the queue on requests that cannot
# succeed.
#
# The decision lives in scripts/rate_limit.py so it can be tested. Only a
# `rejected` status waits: `allowed_warning` means the request went through
# (it reports quota utilisation), and honouring its seven-day resetsAt parked
# the loop for the full MAX_RL_SLEEP ceiling while quota remained -- a batch
# run that looked hung with nothing actually wrong.
# ---------------------------------------------------------------------------
wait_for_rate_limit() {
  local log="$1"
  local secs

  [ -f "$log" ] || return 0

  secs=$(uv run --project "$REPO_ROOT" python3 "$REPO_ROOT/scripts/rate_limit.py" \
           "$log" "${MAX_RL_SLEEP:-7200}") || return 0

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
