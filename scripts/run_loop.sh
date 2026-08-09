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
  GITHUB_OUTPUT="$(mktemp)"; export GITHUB_OUTPUT
  limit="$COUNT"
  [ "$limit" -eq 0 ] && limit=100000     # effectively "the whole queue"

  while [ "${#TICKERS[@]}" -lt "$limit" ]; do
    EXCLUDE="$(IFS=,; echo "${TICKERS[*]:-}")"
    : > "$GITHUB_OUTPUT"
    uv run --project "$REPO_ROOT" python3 scripts/select_ticker.py --override "" --exclude "$EXCLUDE" \
      >/dev/null 2>&1
    T="$(grep '^ticker=' "$GITHUB_OUTPUT" | cut -d= -f2-)"
    [ -z "$T" ] && break
    TICKERS+=("$T")
  done
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
echo ""

if [ "$DRY_RUN" = "1" ]; then
  echo "(--dry-run: nothing executed)"
  exit 0
fi

START=$(date +%s)

# --line-buffer + --tagstring keep concurrent output attributable per ticker.
# --resume reads the joblog and skips arguments that already completed.
printf '%s\n' "${TICKERS[@]}" \
  | parallel -j "$JOBS" --joblog "$JOBLOG" $RESUME \
             --line-buffer --tagstring '[{}]' \
             "$REPO_ROOT/scripts/research_one.sh" {}
PAR_RC=$?

ELAPSED=$(( $(date +%s) - START ))

echo ""
echo "=============================================="
printf ' Finished in %dh%02dm\n' $((ELAPSED / 3600)) $(((ELAPSED % 3600) / 60))
if [ -f "$JOBLOG" ]; then
  # Column 7 of parallel's joblog is the exit status.
  ok=$(awk 'NR>1 && $7==0' "$JOBLOG" | wc -l | tr -d ' ')
  bad=$(awk 'NR>1 && $7!=0' "$JOBLOG" | wc -l | tr -d ' ')
  echo " Succeeded: $ok   Failed: $bad"
  if [ "${bad:-0}" -gt 0 ]; then
    echo " Failed tickers:"
    awk 'NR>1 && $7!=0 {print "   " $NF}' "$JOBLOG"
    echo " Re-run them with: scripts/run_loop.sh <TICKER>..."
  fi
fi
echo "=============================================="

exit "$PAR_RC"
