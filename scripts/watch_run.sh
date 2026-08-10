#!/usr/bin/env bash
# Watch a parallel research run: one tmux pane per ticker.
#
# `run_loop.sh -j 4` merges four traces into one terminal. Per-line tags make
# the output attributable but not readable -- you cannot follow one ticker, and
# a stalled one looks exactly like a busy one. This gives each ticker its own
# pane with its own scrollback, plus a status pane for the batch as a whole.
#
# Usage:
#   watch_run.sh                 # panes for the tickers active right now
#   watch_run.sh AAA.NZ BBB.NZ   # panes for named tickers
#   watch_run.sh --status-only   # just the summary table, no per-ticker panes
#
# This only READS. Workers write $LOG_DIR/{TICKER}.stream and the panes tail
# it, so the viewer can be opened late, closed, or skipped entirely without
# touching a running batch. Nothing in the research path depends on tmux.
#
# Ctrl-b z zooms the focused pane full-screen; Ctrl-b [ scrolls it.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

LOG_DIR="${LOG_DIR:-state/logs}"
SESSION="research-watch"
MAX_PANES=4                 # beyond this the panes are too thin to read
IDLE_CUTOFF=$((6 * 3600))   # a stream older than this is a previous run
STATUS_ONLY=0
TICKERS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --status-only) STATUS_ONLY=1; shift ;;
    --session)     SESSION="$2"; shift 2 ;;
    -h|--help)     sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)            echo "Unknown option: $1" >&2; exit 2 ;;
    *)             TICKERS+=("$1"); shift ;;
  esac
done

command -v tmux >/dev/null || {
  echo "watch_run.sh needs tmux (brew install tmux)." >&2
  echo "Without it, run_loop.sh still prints a tagged trace to stdout." >&2
  exit 1; }

status_cmd() {
  printf '%s' "uv run --project '$REPO_ROOT' python3 \
'$REPO_ROOT/scripts/run_status.py' --watch"
}

if [ "$STATUS_ONLY" = "1" ]; then
  exec uv run --project "$REPO_ROOT" python3 \
    "$REPO_ROOT/scripts/run_status.py" --watch
fi

# Default to whatever is actually moving: a stream file touched recently.
# `find -newermt` is not portable to BSD find, so compare mtimes in bash.
if [ ${#TICKERS[@]} -eq 0 ]; then
  now=$(date +%s)
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    mt=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0)
    [ $((now - mt)) -le "$IDLE_CUTOFF" ] || continue
    t="$(basename "$f")"; t="${t%.stream}"
    TICKERS+=("$t")
  done < <(find "$LOG_DIR" -maxdepth 1 -name '*.stream' 2>/dev/null | sort)
fi

if [ ${#TICKERS[@]} -eq 0 ]; then
  echo "No active run found (no recent $LOG_DIR/*.stream)."
  echo "Showing the status table only; start a run with: make run"
  exec uv run --project "$REPO_ROOT" python3 \
    "$REPO_ROOT/scripts/run_status.py" --watch
fi

if [ ${#TICKERS[@]} -gt "$MAX_PANES" ]; then
  echo "note: ${#TICKERS[@]} tickers active; showing the first $MAX_PANES." \
       "Use 'watch_run.sh TICKER...' to pick others."
  TICKERS=("${TICKERS[@]:0:$MAX_PANES}")
fi

tmux kill-session -t "$SESSION" 2>/dev/null

# tail -F (not -f) survives the truncation research_ticker does at start.
first="${TICKERS[0]}"
tmux new-session -d -s "$SESSION" -n batch \
  "tail -F '$LOG_DIR/$first.stream'"

# Panes are addressed by #{pane_id}: windows are 1-indexed in some configs,
# so a hardcoded "$SESSION:0.0" fails with "can't find window: 0".
pane_id="$(tmux list-panes -t "$SESSION" -F '#{pane_id}' | head -1)"
tmux select-pane -t "$pane_id" -T "$first"

for t in "${TICKERS[@]:1}"; do
  new_id="$(tmux split-window -t "$SESSION" -P -F '#{pane_id}' \
              "tail -F '$LOG_DIR/$t.stream'")"
  tmux select-pane -t "$new_id" -T "$t"
  tmux select-layout -t "$SESSION" tiled >/dev/null
done

# The status pane last, so it is the small one at the end of the layout.
status_id="$(tmux split-window -t "$SESSION" -P -F '#{pane_id}' "$(status_cmd)")"
tmux select-pane -t "$status_id" -T "batch status"

tmux select-layout -t "$SESSION" tiled >/dev/null
tmux set-option -t "$SESSION" pane-border-status top >/dev/null
# Keep a finished ticker's output on screen instead of closing its pane.
tmux set-option -t "$SESSION" remain-on-exit on >/dev/null

if [ -n "${TMUX:-}" ]; then
  # Already inside tmux: switching beats nesting.
  tmux switch-client -t "$SESSION"
else
  tmux attach-session -t "$SESSION"
fi
