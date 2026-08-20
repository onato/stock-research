#!/usr/bin/env bash
# Watch a parallel research run: one tmux pane per ticker.
#
# `run_loop.sh -j 4` merges four traces into one terminal. Per-line tags make
# the output attributable but not readable -- you cannot follow one ticker, and
# a stalled one looks exactly like a busy one. This gives each ticker its own
# pane with its own scrollback, plus a status pane for the batch as a whole.
#
# Usage:
#   watch_run.sh                 # panes for running tickers, recycled as the
#                                # queue advances (a finished ticker's pane is
#                                # retargeted to the next one that starts)
#   watch_run.sh AAA.NZ BBB.NZ   # pin panes to named tickers (no recycling)
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
WINDOW_NAME="batch"
MAX_PANES=4                 # beyond this the panes are too thin to read
RECONCILE_INTERVAL=10       # seconds between reconciler polls
STATUS_ONLY=0
AUTO=0                      # 1 = tickers were discovered, so recycle panes
RECONCILE_TARGET=""
# Inside tmux, default to a window in the CURRENT session: a separate session
# would take over the client and strand prefix-p. --session forces the old
# behavior; outside tmux there is no current session, so a new one is the
# only option.
WINDOW_MODE=0
[ -n "${TMUX:-}" ] && WINDOW_MODE=1
TICKERS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --status-only) STATUS_ONLY=1; shift ;;
    --window)      WINDOW_MODE=1; shift ;;
    --session)     SESSION="$2"; WINDOW_MODE=0; shift 2 ;;
    --new-session) WINDOW_MODE=0; shift ;;
    --reconcile)   RECONCILE_TARGET="$2"; shift 2 ;;  # internal (status pane)
    -h|--help)     sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)            echo "Unknown option: $1" >&2; exit 2 ;;
    *)             TICKERS+=("$1"); shift ;;
  esac
done

# Outside tmux a window cannot be created -- there is no client to attach it to.
[ -z "${TMUX:-}" ] && WINDOW_MODE=0

command -v tmux >/dev/null || {
  echo "watch_run.sh needs tmux (brew install tmux)." >&2
  echo "Without it, run_loop.sh still prints a tagged trace to stdout." >&2
  exit 1; }

status_cmd() {
  printf '%s' "uv run --project '$REPO_ROOT' python3 \
'$REPO_ROOT/scripts/run_status.py' --watch"
}

# Tickers with a live worker right now, freshest first. run_status.py decides
# from the transcript (a log with no result event is running), which is the
# same signal its table shows -- the panes and the table can never disagree.
active_list() {
  uv run --project "$REPO_ROOT" python3 \
    "$REPO_ROOT/scripts/run_status.py" --active 2>/dev/null
}

# in_list NEEDLE [WORD...] -- succeeds when NEEDLE is one of the words.
in_list() {
  local needle="$1" x; shift
  for x in "$@"; do [ "$x" = "$needle" ] && return 0; done
  return 1
}

# ---------------------------------------------------------------------------
# reconcile_loop <window_id>
#
# The batch outlives the panes: GNU parallel starts the next ticker the moment
# one finishes, so a static viewer goes stale one ticker at a time. This loop
# retargets the pane of a finished ticker to the newest one without a pane.
#
# Fail-safe by construction: a pane is only ever touched to satisfy a MISSING
# ACTIVE ticker. If run_status.py errors out, the active list is empty, so
# nothing is missing and nothing is evicted. A pane whose ticker is still
# active is never stolen.
# ---------------------------------------------------------------------------
reconcile_loop() {
  local target="$1"
  local t pid title i a v new_id prev changed

  while :; do
    # The viewer is gone; so are we.
    tmux list-panes -t "$target" >/dev/null 2>&1 || exit 0

    local active=()
    while IFS= read -r t; do
      [ -n "$t" ] && active+=("$t")
    done < <(active_list)

    local pane_ids=() pane_titles=()
    while IFS=' ' read -r pid title; do
      [ "$title" = "batch status" ] && continue
      pane_ids+=("$pid"); pane_titles+=("$title")
    done < <(tmux list-panes -t "$target" -F '#{pane_id} #{pane_title}')

    # Active tickers with no pane, freshest first.
    local missing=()
    for a in ${active[@]+"${active[@]}"}; do
      in_list "$a" ${pane_titles[@]+"${pane_titles[@]}"} || missing+=("$a")
    done

    if [ ${#missing[@]} -gt 0 ]; then
      # Panes whose ticker is no longer active -- safe to retarget.
      local victims=()
      for i in ${pane_ids[@]+"${!pane_ids[@]}"}; do
        in_list "${pane_titles[$i]}" ${active[@]+"${active[@]}"} \
          || victims+=("${pane_ids[$i]}")
      done

      # select-pane -T moves focus, so remember where the user was.
      prev="$(tmux display-message -p -t "$target" '#{pane_id}' 2>/dev/null)"
      changed=0
      for a in "${missing[@]}"; do
        if [ ${#victims[@]} -gt 0 ]; then
          v="${victims[0]}"; victims=("${victims[@]:1}")
          tmux respawn-pane -k -t "$v" \
            "tail -F '$LOG_DIR/$a.stream'" 2>/dev/null || continue
          tmux select-pane -t "$v" -T "$a"
          changed=1
        elif [ ${#pane_ids[@]} -lt "$MAX_PANES" ]; then
          new_id="$(tmux split-window -d -t "$target" -P -F '#{pane_id}' \
                      "tail -F '$LOG_DIR/$a.stream'")" || continue
          tmux select-pane -t "$new_id" -T "$a"
          tmux select-layout -t "$target" tiled >/dev/null
          pane_ids+=("$new_id")
          changed=1
        else
          break   # more active tickers than panes; the status pane has them
        fi
      done
      [ "$changed" = "1" ] && [ -n "$prev" ] && \
        tmux select-pane -t "$prev" 2>/dev/null
    fi

    sleep "$RECONCILE_INTERVAL"
  done
}

# Internal mode for the status pane: reconcile in the background, show the
# table in the foreground. Both die with the pane (kill-window SIGHUPs the
# pane's process group), and the loop also exits once the window is gone.
if [ -n "$RECONCILE_TARGET" ]; then
  reconcile_loop "$RECONCILE_TARGET" &
  exec uv run --project "$REPO_ROOT" python3 \
    "$REPO_ROOT/scripts/run_status.py" --watch
fi

if [ "$STATUS_ONLY" = "1" ]; then
  exec uv run --project "$REPO_ROOT" python3 \
    "$REPO_ROOT/scripts/run_status.py" --watch
fi

# Default to whatever is actually running, freshest first, and keep the view
# current from then on (AUTO=1 starts the reconciler). An explicit ticker
# list pins the panes instead.
if [ ${#TICKERS[@]} -eq 0 ]; then
  AUTO=1
  while IFS= read -r t; do
    [ -n "$t" ] && TICKERS+=("$t")
  done < <(active_list)
fi

if [ ${#TICKERS[@]} -eq 0 ]; then
  echo "No active run found (no running ticker in $LOG_DIR)."
  echo "Showing the status table only; start a run with: make run"
  exec uv run --project "$REPO_ROOT" python3 \
    "$REPO_ROOT/scripts/run_status.py" --watch
fi

if [ ${#TICKERS[@]} -gt "$MAX_PANES" ]; then
  echo "note: ${#TICKERS[@]} tickers active; showing the first $MAX_PANES." \
       "Use 'watch_run.sh TICKER...' to pick others."
  TICKERS=("${TICKERS[@]:0:$MAX_PANES}")
fi

# tail -F (not -f) survives the truncation research_ticker does at start.
first="${TICKERS[0]}"

if [ "$WINDOW_MODE" = "1" ]; then
  # Inside tmux: build a WINDOW in the current session. `switch-client` to a
  # separate session looks similar but replaces the session you are in, so
  # `Ctrl-b p` then has nothing to cycle back to -- the viewer appears to
  # swallow your shell. A window keeps prefix-p/n/w working as usual.
  tmux kill-window -t "$WINDOW_NAME" 2>/dev/null
  target="$(tmux new-window -P -F '#{window_id}' -n "$WINDOW_NAME" \
              "tail -F '$LOG_DIR/$first.stream'")"
else
  tmux kill-session -t "$SESSION" 2>/dev/null
  tmux new-session -d -s "$SESSION" -n "$WINDOW_NAME" \
    "tail -F '$LOG_DIR/$first.stream'"
  # The reconciler needs a rename-proof, quoting-safe handle on this window;
  # a window id (@N) is both, where a session name may not be.
  target="$(tmux list-windows -t "$SESSION" -F '#{window_id}' | head -1)"
fi

# Panes are addressed by #{pane_id}: windows are 1-indexed in some configs,
# so a hardcoded "$SESSION:0.0" fails with "can't find window: 0".
pane_id="$(tmux list-panes -t "$target" -F '#{pane_id}' | head -1)"
tmux select-pane -t "$pane_id" -T "$first"

for t in "${TICKERS[@]:1}"; do
  new_id="$(tmux split-window -t "$target" -P -F '#{pane_id}' \
              "tail -F '$LOG_DIR/$t.stream'")"
  tmux select-pane -t "$new_id" -T "$t"
  tmux select-layout -t "$target" tiled >/dev/null
done

# The status pane last, so it is the small one at the end of the layout. In
# auto mode it also hosts the reconciler as a background child, so the pane
# recycling lives and dies with the viewer -- close the window and both stop.
if [ "$AUTO" = "1" ]; then
  pane_cmd="'$REPO_ROOT/scripts/watch_run.sh' --reconcile $target"
else
  pane_cmd="$(status_cmd)"
fi
status_id="$(tmux split-window -t "$target" -P -F '#{pane_id}' "$pane_cmd")"
tmux select-pane -t "$status_id" -T "batch status"

tmux select-layout -t "$target" tiled >/dev/null
tmux set-option -t "$target" pane-border-status top >/dev/null
# Keep a finished ticker's output on screen instead of closing its pane.
tmux set-option -t "$target" remain-on-exit on >/dev/null

if [ "$WINDOW_MODE" = "1" ]; then
  tmux select-window -t "$target"      # already here; just focus it
elif [ -n "${TMUX:-}" ]; then
  tmux switch-client -t "$SESSION"
else
  tmux attach-session -t "$SESSION"
fi
