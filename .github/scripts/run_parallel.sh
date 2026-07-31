#!/usr/bin/env bash
# Parallel local screener: research N tickers concurrently, but let only
# one process touch git at a time.
#
# The research phase is almost entirely waiting -- PDF downloads, pdftotext,
# web search, model round-trips -- so concurrency costs little CPU. The real
# ceiling is your Claude rate limit: 4 concurrent runs burn tokens ~4x as
# fast, and once you hit the limit they all slow down together. If runs
# start stalling, lower --jobs rather than raising it.
#
# Usage:
#   .github/scripts/run_parallel.sh -n 8              # 8 tickers, 4 at a time
#   .github/scripts/run_parallel.sh -n 8 -j 2         # ...2 at a time
#   .github/scripts/run_parallel.sh AAA.NZ BBB.NZ     # specific tickers
#   .github/scripts/run_parallel.sh -n 4 --no-push    # commit, do not push
#   .github/scripts/run_parallel.sh -n 4 --ignore-budget
#
# Logs land in .github/state/logs/{TICKER}.log -- with parallel output
# interleaved, that is where to look when one ticker misbehaves.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

JOBS=4
COUNT=0
PUSH=1
BUDGET_ARGS=""
TICKERS=()
LOG_DIR=".github/state/logs"
GIT_LOCK=".github/state/git.lock"

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--count)      COUNT="$2"; shift 2 ;;
    -j|--jobs)       JOBS="$2"; shift 2 ;;
    --no-push)       PUSH=0; shift ;;
    --ignore-budget) BUDGET_ARGS="--ignore-budget"; shift ;;
    -h|--help)       sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)              echo "Unknown option: $1" >&2; exit 2 ;;
    *)               TICKERS+=("$1"); shift ;;
  esac
done

command -v claude    >/dev/null || { echo "ERROR: claude CLI not found" >&2; exit 1; }
command -v pdftotext >/dev/null || { echo "ERROR: pdftotext not found (brew install poppler)" >&2; exit 1; }
command -v flock     >/dev/null || FLOCK_MISSING=1   # macOS lacks flock(1); fallback below

mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# Reserve the ticker list UP FRONT, single-threaded.
#
# select_ticker.py decides by looking at which Reports/ dirs exist, so two
# concurrent selectors would both pick the same ticker. Claiming the whole
# batch before any research starts avoids that entirely -- and makes the
# batch visible to you before 4 long jobs launch.
# ---------------------------------------------------------------------------
if [ ${#TICKERS[@]} -eq 0 ]; then
  [ "$COUNT" -eq 0 ] && COUNT=$JOBS
  GITHUB_OUTPUT="$(mktemp)"; export GITHUB_OUTPUT

  for _ in $(seq 1 "$COUNT"); do
    : > "$GITHUB_OUTPUT"
    python3 .github/scripts/guard.py --max-runs 8 --ignore-weekend $BUDGET_ARGS >/dev/null 2>&1
    if [ "$(grep '^proceed=' "$GITHUB_OUTPUT" | cut -d= -f2)" != "true" ]; then
      echo "Budget exhausted after reserving ${#TICKERS[@]} ticker(s). Pass --ignore-budget to override."
      break
    fi

    # Pass what we already claimed: an empty Reports/ dir deliberately does
    # NOT mark a ticker as taken (that means "a previous run died, retry
    # it"), so the selector cannot infer the reservation on its own.
    EXCLUDE="$(IFS=,; echo "${TICKERS[*]:-}")"
    : > "$GITHUB_OUTPUT"
    python3 .github/scripts/select_ticker.py --override "" --exclude "$EXCLUDE" >/dev/null 2>&1
    T="$(grep '^ticker=' "$GITHUB_OUTPUT" | cut -d= -f2-)"
    [ -z "$T" ] && { echo "Queue exhausted."; break; }

    TICKERS+=("$T")
  done
  rm -f "$GITHUB_OUTPUT"
fi

if [ ${#TICKERS[@]} -eq 0 ]; then
  echo "Nothing to do."
  exit 0
fi

echo "Researching ${#TICKERS[@]} ticker(s), ${JOBS} at a time:"
printf '  %s\n' "${TICKERS[@]}"
echo "Logs: $LOG_DIR/"
echo ""

# ---------------------------------------------------------------------------
# Serialized commit. flock(1) is absent on stock macOS, so fall back to an
# mkdir spinlock -- mkdir is atomic on every POSIX filesystem.
# ---------------------------------------------------------------------------
commit_locked() {
  local ticker="$1" lockdir="${GIT_LOCK}.d" waited=0
  while ! mkdir "$lockdir" 2>/dev/null; do
    sleep 2
    waited=$((waited + 2))
    if [ "$waited" -gt 600 ]; then
      echo "[$ticker] Could not acquire git lock after 10m; skipping commit." >&2
      return 1
    fi
  done
  # shellcheck disable=SC2064
  trap "rmdir '$lockdir' 2>/dev/null" RETURN

  git add -A -- "$ticker" index.html .github/state/budget.json 2>/dev/null
  if git diff --cached --quiet; then
    echo "[$ticker] No output produced -- nothing to commit."
    return 0
  fi
  git commit -q -m "feat(screener): research for $ticker

Automated parallel local run via .github/scripts/run_parallel.sh"
  echo "[$ticker] Committed."
  if [ "$PUSH" = "1" ]; then
    git pull -q --rebase --autostash && git push -q && echo "[$ticker] Pushed."
  fi
}

research_one() {
  local ticker="$1" log="$LOG_DIR/$1.log"
  echo "[$ticker] Starting..."
  if claude --permission-mode bypassPermissions -p "/research-stock $ticker" > "$log" 2>&1; then
    echo "[$ticker] Research finished."
  else
    echo "[$ticker] Research exited non-zero -- see $log" >&2
  fi
  commit_locked "$ticker"
}

# Bounded fan-out: keep at most $JOBS research processes alive.
for t in "${TICKERS[@]}"; do
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do sleep 2; done
  research_one "$t" &
done
wait

rmdir "${GIT_LOCK}.d" 2>/dev/null
echo ""
echo "Done. ${#TICKERS[@]} ticker(s) processed."
