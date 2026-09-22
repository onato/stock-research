"""watch_run.sh drives a terminal multiplexer; these tests drive it with stubs.

The viewer is shell, but it is still deterministic code under scripts/: every
multiplexer call goes through a `tmux` or `herdr` binary on PATH, so a stub
that records its argv (and answers with canned JSON) pins the behavior without
a live server. `uv` is stubbed too -- it is only the launcher for
run_status.py, whose own behavior is covered in test_run_status.py.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "watch_run.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None,
                                reason="the herdr backend parses JSON with jq")

HERDR_STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/herdr.calls"
case "$1 $2" in
  "tab list")    cat "$STUB_DIR/tabs.json" ;;
  "tab create")  echo '{"result":{"tab":{"tab_id":"w1:t9"},"root_pane":{"pane_id":"w1:p10"}}}' ;;
  "pane list")   cat "$STUB_DIR/panes.json" ;;
  "pane split")  n=$(( $(cat "$STUB_DIR/next") + 1 )); echo "$n" > "$STUB_DIR/next"
                 echo "{\"result\":{\"pane\":{\"pane_id\":\"w1:p$n\"}}}" ;;
  *)             echo '{"result":{"type":"ok"}}' ;;
esac
"""

TMUX_STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/tmux.calls"
case "$1" in
  display-message) echo "$STUB_TMUX_PID" ;;
  new-window)      echo "@7" ;;
  list-windows)    echo "@7" ;;
  list-panes)      echo "%1" ;;
  split-window)    echo "%2" ;;
esac
"""

UV_STUB = r"""#!/usr/bin/env bash
case "$*" in *--active*) cat "$STUB_DIR/active" ;; esac
"""


@pytest.fixture
def stubs(tmp_path):
    for name, body in (("herdr", HERDR_STUB), ("tmux", TMUX_STUB),
                       ("uv", UV_STUB)):
        path = tmp_path / name
        path.write_text(body)
        path.chmod(0o755)
    (tmp_path / "next").write_text("10")
    (tmp_path / "tabs.json").write_text('{"result":{"tabs":[]}}')
    (tmp_path / "panes.json").write_text('{"result":{"panes":[]}}')
    (tmp_path / "active").write_text("AAA.NZ\nBBB.NZ\n")
    return tmp_path


def run(stubs, *args, **env):
    full = {"PATH": f"{stubs}:{os.environ['PATH']}", "HOME": str(stubs),
            "STUB_DIR": str(stubs), "STUB_TMUX_PID": "123", **env}
    done = subprocess.run(["bash", str(SCRIPT), *args], env=full,
                          capture_output=True, text=True, timeout=30,
                          check=False)
    assert done.returncode == 0, done.stderr
    return done


def calls(stubs, name):
    log = stubs / f"{name}.calls"
    return log.read_text().splitlines() if log.exists() else []


HERDR_ENV = {"HERDR_ENV": "1", "HERDR_WORKSPACE_ID": "w1"}


class TestHerdrBackend:
    def test_inside_herdr_the_viewer_is_a_herdr_tab_not_tmux(self, stubs):
        # A stale $TMUX survives into herdr panes; it must not win.
        run(stubs, **HERDR_ENV, TMUX="/tmp/tmux-0/default,999,0")
        assert calls(stubs, "tmux") == []
        made = calls(stubs, "herdr")
        assert any(c.startswith("tab create") and "--label batch" in c
                   for c in made)
        assert "tab focus w1:t9" in made

    def test_each_ticker_gets_a_labelled_pane_tailing_its_stream(self, stubs):
        run(stubs, **HERDR_ENV)
        made = calls(stubs, "herdr")
        # p10 is the tab's root pane, p11 the status strip, p12 the 2nd ticker.
        assert "pane run w1:p10 tail -F 'state/logs/AAA.NZ.stream'" in made
        assert "pane rename w1:p10 AAA.NZ" in made
        assert "pane run w1:p12 tail -F 'state/logs/BBB.NZ.stream'" in made
        assert "pane rename w1:p12 BBB.NZ" in made

    def test_auto_mode_hosts_the_reconciler_in_the_status_pane(self, stubs):
        run(stubs, **HERDR_ENV)
        made = calls(stubs, "herdr")
        assert "pane rename w1:p11 batch status" in made
        assert any(c.startswith("pane run w1:p11 ")
                   and c.endswith("--reconcile w1:t9") for c in made)

    def test_pinned_tickers_get_the_plain_status_table(self, stubs):
        run(stubs, "ZZZ.NZ", **HERDR_ENV)
        made = calls(stubs, "herdr")
        assert "pane run w1:p10 tail -F 'state/logs/ZZZ.NZ.stream'" in made
        assert not any("--reconcile" in c for c in made)

    def test_a_previous_viewer_tab_is_replaced(self, stubs):
        (stubs / "tabs.json").write_text(json.dumps({"result": {"tabs": [
            {"tab_id": "w1:t1", "label": "1"},
            {"tab_id": "w1:t4", "label": "batch"}]}}))
        run(stubs, **HERDR_ENV)
        made = calls(stubs, "herdr")
        assert "tab close w1:t4" in made
        assert "tab close w1:t1" not in made

    def test_a_finished_tickers_pane_is_retargeted(self, stubs):
        (stubs / "panes.json").write_text(json.dumps({"result": {"panes": [
            {"pane_id": "w1:p10", "tab_id": "w1:t9", "label": "OLD.NZ"},
            {"pane_id": "w1:p12", "tab_id": "w1:t9", "label": "BBB.NZ"},
            {"pane_id": "w1:p11", "tab_id": "w1:t9", "label": "batch status"},
            {"pane_id": "w1:p1", "tab_id": "w1:t1"}]}}))
        run(stubs, "--reconcile-once", "w1:t9", **HERDR_ENV)
        made = calls(stubs, "herdr")
        assert "pane send-keys w1:p10 ctrl+c" in made
        assert ("pane run w1:p10 clear; tail -F 'state/logs/AAA.NZ.stream'"
                in made)
        assert "pane rename w1:p10 AAA.NZ" in made
        # BBB.NZ is still running: its pane is never stolen.
        assert not any(c.startswith("pane send-keys w1:p12") for c in made)

    def test_a_new_ticker_gets_a_new_pane_while_there_is_room(self, stubs):
        (stubs / "panes.json").write_text(json.dumps({"result": {"panes": [
            {"pane_id": "w1:p10", "tab_id": "w1:t9", "label": "BBB.NZ"},
            {"pane_id": "w1:p11", "tab_id": "w1:t9",
             "label": "batch status"}]}}))
        (stubs / "next").write_text("20")
        run(stubs, "--reconcile-once", "w1:t9", **HERDR_ENV)
        made = calls(stubs, "herdr")
        assert any(c.startswith("pane split w1:p10 --direction right")
                   and "--no-focus" in c for c in made)
        assert "pane run w1:p21 tail -F 'state/logs/AAA.NZ.stream'" in made
        assert "pane rename w1:p21 AAA.NZ" in made


class TestTmuxBackend:
    def test_a_live_tmux_gets_a_window_in_the_current_session(self, stubs):
        run(stubs, TMUX="/tmp/tmux-0/default,123,0")
        made = calls(stubs, "tmux")
        assert any(c.startswith("new-window") for c in made)
        assert not any(c.startswith("new-session") for c in made)

    def test_a_stale_tmux_variable_falls_back_to_a_new_session(self, stubs):
        # $TMUX names server pid 999 but the socket answers as 123 (or not at
        # all): the client we were started in is gone, so there is no current
        # session to add a window to.
        run(stubs, TMUX="/tmp/tmux-0/default,999,0")
        made = calls(stubs, "tmux")
        assert any(c.startswith("new-session") for c in made)
        assert not any(c.startswith("new-window") for c in made)
