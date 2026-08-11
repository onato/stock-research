#!/usr/bin/env bash
# Continuously drain the ticker queue, N tickers at a time.
#
# As soon as one ticker finishes, the next starts -- GNU parallel keeps the
# slots full rather than waiting for a whole batch to complete. Interrupt it
# at any point; --resume picks up where it left off.
#
# Usage:
#   run_loop.sh                  # drain the whole queue, 4 at a time
#   run_loop.sh -n 20            # stop after 20 tickers
#   run_loop.sh -j 2             # 2 concurrent instead of 4
#   run_loop.sh --no-push        # commit locally, don't push
#   run_loop.sh --dry-run        # print the queue and exit (no Claude, no cost)
#   run_loop.sh AAA.NZ BBB.NZ    # specific tickers instead of the queue
#   run_loop.sh --force AAA.NZ   # redo a ticker that is already researched
#   run_loop.sh --no-resume      # re-run tickers a prior run already finished
#
# Defaults: --resume is ON, and already-researched tickers are skipped unless
# they are stale (older than --stale-days, default 45). Both apply whether the
# tickers come from the queue or the command line -- passing them explicitly
# used to bypass every filter, which re-ran finished work and ignored -n.
#
# Bounded by the queue, not by a budget: it stops when the queue is empty or
# -n is reached. Rate limits are handled by pausing until reset (see lib.sh),
# so an overnight drain survives a limit window instead of failing through it.
#
# Per-ticker transcripts: state/logs/{TICKER}.log
# Scheduling record:      state/joblog.tsv

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

JOBS=4
COUNT=0                 # 0 = drain the whole queue
PUSH=1
RESUME="--resume"       # on by default: an interrupted drain should continue
DRY_RUN=0
FORCE=0
STALE_DAYS=45
TICKERS=()
LOG_DIR="state/logs"
JOBLOG="state/joblog.tsv"

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--count)   COUNT="$2"; shift 2 ;;
    -j|--jobs)    JOBS="$2"; shift 2 ;;
    --no-push)    PUSH=0; shift ;;
    --resume)     RESUME="--resume"; shift ;;   # kept: harmless, now default
    --no-resume)  RESUME=""; shift ;;
    --force)      FORCE=1; shift ;;
    --stale-days) STALE_DAYS="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)           echo "Unknown option: $1" >&2; exit 2 ;;
    *)            TICKERS+=("$1"); shift ;;
  esac
done

export PUSH LOG_DIR
. "$REPO_ROOT/scripts/lib.sh"
require_tools
command -v parallel >/dev/null || {
  echo "ERROR: GNU parallel not found (brew install parallel)" >&2; exit 1; }

mkdir -p "$LOG_DIR" "$(dirname "$JOBLOG")"

# ---------------------------------------------------------------------------
# Build the work list up front, single-threaded.
#
# select_ticker.py decides from which Reports/ dirs exist, so two concurrent
# selectors would hand out the same ticker. Reserving the list before any
# work starts avoids that -- and lets you see what is about to run.
#
# An empty Reports/ dir deliberately does NOT mark a ticker as taken (that
# means "a previous run died, retry it"), so --exclude carries the claim.
# ---------------------------------------------------------------------------
if [ ${#TICKERS[@]} -eq 0 ]; then
  limit="$COUNT"
  # No -n means "drain the queue". Reserving all 1748 unresearched tickers
  # took ~2min of silence before any output -- indistinguishable from a hang
  # -- so an unbounded run reserves a working set and picks up the rest on
  # the next invocation (--resume is on by default).
  if [ "$limit" -eq 0 ]; then
    limit=200
    echo "No -n given: reserving up to $limit ticker(s). Re-run to continue."
  fi
  echo "Building queue..."

  GITHUB_OUTPUT="$(mktemp)"; export GITHUB_OUTPUT
  uv run --project "$REPO_ROOT" python3 scripts/select_ticker.py \
    --override "" --count "$limit" >/dev/null 2>&1
  mapfile -t TICKERS < <(grep '^ticker=' "$GITHUB_OUTPUT" | cut -d= -f2- | grep -v '^$')
  rm -f "$GITHUB_OUTPUT"
else
  # Tickers named on the command line get the same policy the queue does:
  # drop anything already researched and still fresh, drop lines that are not
  # tickers at all (state/backlog.txt carries a prose GAP note), and honour
  # -n. Without this, `run_loop.sh -n 20 $(cat state/backlog.txt)` re-ran six
  # finished tickers, ignored the limit, and queued all 782 entries.
  SUPPLIED=${#TICKERS[@]}
  FORCE_FLAG=()
  [ "$FORCE" = "1" ] && FORCE_FLAG=(--force)
  mapfile -t TICKERS < <(
    uv run --project "$REPO_ROOT" python3 scripts/filter_tickers.py \
      --limit "$COUNT" --stale-days "$STALE_DAYS" \
      "${FORCE_FLAG[@]}" "${TICKERS[@]}")
  skipped=$(( SUPPLIED - ${#TICKERS[@]} ))
  if [ "$skipped" -gt 0 ]; then
    echo "Skipping $skipped of $SUPPLIED supplied (already researched, not a" \
         "ticker, or beyond -n). Use --force to re-research."
  fi
fi

if [ ${#TICKERS[@]} -eq 0 ]; then
  echo "Nothing to do -- queue exhausted and no DCFs to refresh."
  exit 0
fi

echo "Queue: ${#TICKERS[@]} ticker(s), ${JOBS} at a time"
printf '  %s\n' "${TICKERS[@]}" | head -12
[ "${#TICKERS[@]}" -gt 12 ] && echo "  ... and $(( ${#TICKERS[@]} - 12 )) more"
echo "Logs:   $LOG_DIR/"
echo "Joblog: $JOBLOG"
# Concurrent traces share one terminal, which is readable only up to a point.
# Suggested rather than launched: opening windows unasked is a nuisance.
if [ -n "${TMUX:-}" ] && [ "$JOBS" -gt 1 ]; then
  echo "Watch:  scripts/watch_run.sh   (one pane per ticker)"
fi
echo ""

if [ "$DRY_RUN" = "1" ]; then
  echo "(--dry-run: nothing executed)"
  exit 0
fi

START=$(date +%s)

# GNU parallel's --resume skips by SEQUENCE NUMBER, not by argument: with a
# shared joblog, job #1 of a new run is "already done" because job #1 of an
# older run is recorded. That made every subsequent invocation exit in 0h00m
# without running anything. Each run therefore gets its own joblog, which
# preserves resume WITHIN a run (interrupt and re-run with the same file)
# while a new run always starts clean. The stable path stays as the record
# of the most recent run, for the summary below and `make status`.
RUN_JOBLOG="${JOBLOG%.tsv}.$(date +%Y%m%d-%H%M%S).tsv"

# Cleared per run: a leftover halt from a previous batch would make every
# ticker exit immediately. research_one.sh creates it when it meets a window
# too far out to wait for, and the other workers stand down rather than
# failing identically one slot at a time.
HALT_FILE="$LOG_DIR/.halt-rate-limit"
export HALT_FILE
rm -f "$HALT_FILE"

# --line-buffer + --tagstring keep concurrent output attributable per ticker.
# --resume reads the joblog and skips arguments that already completed.
printf '%s\n' "${TICKERS[@]}" \
  | parallel -j "$JOBS" --joblog "$RUN_JOBLOG" $RESUME \
             --line-buffer --tagstring '[{}]' \
             "$REPO_ROOT/scripts/research_one.sh" {}
PAR_RC=$?

# This run's record becomes the current one; the timestamped file is kept so
# an interrupted run can be resumed against it.
cp -f "$RUN_JOBLOG" "$JOBLOG" 2>/dev/null || true

# Exit 4 from any ticker means an unreachable rate-limit window: the rest of
# the queue cannot succeed either, so say so once rather than letting every
# remaining ticker fail the same way.
if awk 'NR>1 && $7==4 {found=1} END{exit !found}' "$RUN_JOBLOG" 2>/dev/null; then
  echo ""
  echo "STOPPED: rate limit resets beyond what a single run can wait out."
  [ -f "$HALT_FILE" ] && sed 's/^/  /' "$HALT_FILE"
  skipped=$(awk 'NR>1 && $7==5' "$RUN_JOBLOG" | wc -l | tr -d ' ')
  [ "${skipped:-0}" -gt 0 ] && \
    echo "  $skipped ticker(s) stood down and are still queued."
  echo "Re-run after the reset; --resume is on by default."
  rm -f "$HALT_FILE"
fi

ELAPSED=$(( $(date +%s) - START ))

echo ""
echo "=============================================="
printf ' Finished in %dh%02dm\n' $((ELAPSED / 3600)) $(((ELAPSED % 3600) / 60))
if [ -f "$RUN_JOBLOG" ]; then
  # This run only -- the shared joblog accumulates every past run, which
  # made the summary report failures from days ago as if they were this run's.
  # Column 7 of parallel's joblog is the exit status.
  ok=$(awk 'NR>1 && $7==0' "$RUN_JOBLOG" | wc -l | tr -d ' ')
  # Exit 5 is "stood down because the run halted" -- those tickers were never
  # attempted and are still queued, so counting them as failures would
  # misreport a rate limit as 173 broken tickers.
  bad=$(awk 'NR>1 && $7!=0 && $7!=5' "$RUN_JOBLOG" | wc -l | tr -d ' ')
  echo " Succeeded: $ok   Failed: $bad"
  if [ "${bad:-0}" -gt 0 ]; then
    echo " Failed tickers:"
    awk 'NR>1 && $7!=0 && $7!=5 {print "   " $NF}' "$RUN_JOBLOG"
    echo " Re-run them with: scripts/run_loop.sh --force <TICKER>..."
  fi
fi
echo "=============================================="

exit "$PAR_RC"
