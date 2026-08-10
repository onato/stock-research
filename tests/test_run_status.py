"""run_status.py summarises a parallel run: one row per ticker.

`run_loop.sh -j 4` merges four traces into one terminal. Per-line tags made
the output attributable but not readable -- you cannot follow one ticker, and
a stalled one looks identical to a busy one. This is the status view that
answers "how is the batch going" without reading four traces.

Two things it must get right:

* **Idle time.** A worker whose log has not moved in minutes is the stalled
  case that is currently invisible. It is the single most useful column.
* **Cost, while the run is still going.** CEN.NZ cost $16.99 and CDI.NZ
  $12.86 on 2026-08-10, both reported as successes, and nobody saw the spend
  until afterwards.

Everything is derived from files the run already writes -- state/logs/*.log
and parallel's joblog -- so there is no new instrumentation to keep in sync.
"""

import json
import sys
import time

import pytest
import run_status


def write_log(tmp_path, ticker, *events, mtime=None):
    d = tmp_path / "state" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{ticker}.log"
    with open(path, "w") as f:
        f.writelines(
            (json.dumps(e) if isinstance(e, dict) else e) + "\n" for e in events)
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))
    return path


def tool(name, **inp):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name,
                                     "input": inp}]}}


def result(cost=None, is_error=False):
    ev = {"type": "result", "is_error": is_error}
    if cost is not None:
        ev["total_cost_usd"] = cost
    return ev


def write_joblog(tmp_path, *rows):
    """rows are (seq, runtime, exitval, command-tail)."""
    path = tmp_path / "state" / "joblog.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "\t".join(["Seq", "Host", "Starttime", "JobRuntime", "Send",
                        "Receive", "Exitval", "Signal", "Command"])
    lines = [header]
    for seq, runtime, exitval, ticker in rows:
        lines.append(f"{seq}\t:\t0\t{runtime}\t0\t0\t{exitval}\t0\t"
                     f"/x/research_one.sh {ticker}")
    path.write_text("\n".join(lines) + "\n")
    return path


class TestTickerRow:
    def test_counts_tool_calls(self, tmp_path):
        write_log(tmp_path, "CO2.NZ", tool("Bash", description="a"),
                  tool("Read", file_path="x"))
        row = run_status.ticker_row(tmp_path, "CO2.NZ")
        assert row["tools"] == 2

    def test_reports_the_last_tool(self, tmp_path):
        # The pane shows the trace; this says what it is *currently* on.
        write_log(tmp_path, "CO2.NZ", tool("Bash", description="early"),
                  tool("Agent", subagent_type="financial-parser",
                       description="adjudicate"))
        row = run_status.ticker_row(tmp_path, "CO2.NZ")
        assert "financial-parser" in row["last_tool"]

    def test_reuses_progress_describe(self, tmp_path):
        # Same rendering as the pane trace, so the two never disagree.
        write_log(tmp_path, "CO2.NZ", tool("Bash", description="fetch filings"))
        assert run_status.ticker_row(tmp_path, "CO2.NZ")["last_tool"] == \
            "$ fetch filings"

    def test_reports_cost_from_the_result_event(self, tmp_path):
        write_log(tmp_path, "CEN.NZ", tool("Bash"), result(cost=16.98553425))
        assert run_status.ticker_row(tmp_path, "CEN.NZ")["cost"] == \
            pytest.approx(16.99, abs=0.01)

    def test_cost_is_none_before_the_result_arrives(self, tmp_path):
        write_log(tmp_path, "CO2.NZ", tool("Bash"))
        assert run_status.ticker_row(tmp_path, "CO2.NZ")["cost"] is None

    def test_idle_seconds_come_from_the_log_mtime(self, tmp_path):
        write_log(tmp_path, "OLD.NZ", tool("Bash"), mtime=time.time() - 600)
        assert run_status.ticker_row(tmp_path, "OLD.NZ")["idle"] >= 590

    def test_a_missing_log_is_pending_not_an_error(self, tmp_path):
        (tmp_path / "state" / "logs").mkdir(parents=True)
        row = run_status.ticker_row(tmp_path, "NOTYET.NZ")
        assert row["tools"] == 0
        assert row["state"] == "pending"

    def test_malformed_lines_are_skipped(self, tmp_path):
        write_log(tmp_path, "ODD.NZ", "not json", tool("Bash"))
        assert run_status.ticker_row(tmp_path, "ODD.NZ")["tools"] == 1


class TestState:
    def test_a_log_with_no_result_is_running(self, tmp_path):
        write_log(tmp_path, "CO2.NZ", tool("Bash"))
        assert run_status.ticker_row(tmp_path, "CO2.NZ")["state"] == "running"

    def test_a_clean_result_is_done(self, tmp_path):
        write_log(tmp_path, "CO2.NZ", tool("Bash"), result(cost=1.0))
        assert run_status.ticker_row(tmp_path, "CO2.NZ")["state"] == "done"

    def test_the_joblog_exit_code_wins(self, tmp_path):
        # parallel's joblog is authoritative once a ticker finishes.
        write_log(tmp_path, "CVT.NZ", tool("Bash"), result(cost=7.64,
                                                           is_error=True))
        write_joblog(tmp_path, (1, 3124, 1, "CVT.NZ"))
        row = run_status.ticker_row(tmp_path, "CVT.NZ",
                                   joblog=tmp_path / "state" / "joblog.tsv")
        assert row["state"] == "failed"
        assert row["elapsed"] == 3124

    def test_exit_zero_is_done_even_when_the_result_says_error(self, tmp_path):
        # CO2.NZ ended on "Connection closed mid-response" but had written
        # every deliverable; research_one.sh exits 0 for that case.
        write_log(tmp_path, "CO2.NZ", tool("Bash"), result(cost=4.44,
                                                           is_error=True))
        write_joblog(tmp_path, (1, 2563, 0, "CO2.NZ"))
        row = run_status.ticker_row(tmp_path, "CO2.NZ",
                                    joblog=tmp_path / "state" / "joblog.tsv")
        assert row["state"] == "done"

    def test_exit_four_is_reported_as_rate_limited(self, tmp_path):
        # rc=4 means "window resets beyond what one run can wait out".
        write_log(tmp_path, "DOW.NZ", tool("Bash"))
        write_joblog(tmp_path, (1, 60, 4, "DOW.NZ"))
        row = run_status.ticker_row(tmp_path, "DOW.NZ",
                                    joblog=tmp_path / "state" / "joblog.tsv")
        assert row["state"] == "rate-limited"

    def test_exit_three_is_reported_as_incomplete(self, tmp_path):
        write_log(tmp_path, "CRP.NZ", tool("Bash"))
        write_joblog(tmp_path, (1, 2573, 3, "CRP.NZ"))
        row = run_status.ticker_row(tmp_path, "CRP.NZ",
                                    joblog=tmp_path / "state" / "joblog.tsv")
        assert row["state"] == "incomplete"


class TestJoblog:
    def test_tickers_come_from_the_command_column(self, tmp_path):
        write_joblog(tmp_path, (1, 10, 0, "A.NZ"), (2, 20, 1, "B.NZ"))
        got = run_status.joblog_rows(tmp_path / "state" / "joblog.tsv")
        assert set(got) == {"A.NZ", "B.NZ"}
        assert got["B.NZ"]["exit"] == 1

    def test_a_missing_joblog_is_empty_not_fatal(self, tmp_path):
        assert run_status.joblog_rows(tmp_path / "nope.tsv") == {}

    def test_a_header_only_joblog_is_empty(self, tmp_path):
        write_joblog(tmp_path)
        assert run_status.joblog_rows(tmp_path / "state" / "joblog.tsv") == {}

    def test_a_truncated_row_is_skipped(self, tmp_path):
        path = tmp_path / "state" / "joblog.tsv"
        path.parent.mkdir(parents=True)
        path.write_text("Seq\tHost\n1\t:\n")
        assert run_status.joblog_rows(path) == {}


class TestDefensivePaths:
    """A status view must never be the thing that breaks a run."""

    def test_a_nonnumeric_exit_column_is_skipped(self, tmp_path):
        path = tmp_path / "state" / "joblog.tsv"
        path.parent.mkdir(parents=True)
        path.write_text(
            "\t".join(["Seq", "Host", "Starttime", "JobRuntime", "Send",
                       "Receive", "Exitval", "Signal", "Command"]) + "\n"
            + "1\t:\t0\tnotanumber\t0\t0\tzero\t0\t/x/r.sh A.NZ\n")
        assert run_status.joblog_rows(path) == {}

    def test_a_command_column_without_a_ticker_is_skipped(self, tmp_path):
        path = tmp_path / "state" / "joblog.tsv"
        path.parent.mkdir(parents=True)
        path.write_text(
            "\t".join(["Seq", "Host", "Starttime", "JobRuntime", "Send",
                       "Receive", "Exitval", "Signal", "Command"]) + "\n"
            + "1\t:\t0\t10\t0\t0\t0\t0\t\n")
        assert run_status.joblog_rows(path) == {}

    def test_an_unreadable_log_yields_a_pending_row(self, tmp_path):
        d = tmp_path / "state" / "logs"
        d.mkdir(parents=True)
        (d / "DIR.NZ.log").mkdir()      # a directory where a file belongs
        row = run_status.ticker_row(tmp_path, "DIR.NZ")
        assert row["tools"] == 0

    def test_string_content_does_not_crash_the_scan(self, tmp_path):
        # stream-json emits plain-string content for simple messages.
        write_log(tmp_path, "A.NZ",
                  {"type": "assistant", "message": {"content": "just text"}},
                  tool("Bash"))
        assert run_status.ticker_row(tmp_path, "A.NZ")["tools"] == 1

    def test_a_non_numeric_cost_is_ignored(self, tmp_path):
        write_log(tmp_path, "A.NZ", tool("Bash"),
                  {"type": "result", "total_cost_usd": "free"})
        assert run_status.ticker_row(tmp_path, "A.NZ")["cost"] is None

    def test_everything_filtered_out_says_so(self, tmp_path):
        write_log(tmp_path, "OLD.NZ", tool("Bash"),
                  mtime=time.time() - 10 * 86400)
        assert "no recent activity" in run_status.render(tmp_path)


class TestDiscovery:
    def test_tickers_are_discovered_from_the_logs(self, tmp_path):
        write_log(tmp_path, "B.NZ", tool("Bash"))
        write_log(tmp_path, "A.NZ", tool("Bash"))
        assert run_status.discover(tmp_path) == ["A.NZ", "B.NZ"]

    def test_stream_files_are_not_mistaken_for_logs(self, tmp_path):
        write_log(tmp_path, "A.NZ", tool("Bash"))
        (tmp_path / "state" / "logs" / "A.NZ.stream").write_text("x\n")
        assert run_status.discover(tmp_path) == ["A.NZ"]

    def test_no_logs_is_empty(self, tmp_path):
        assert run_status.discover(tmp_path) == []


class TestStaleRuns:
    """Only the current run belongs on the table.

    state/logs/ accumulates every transcript ever written -- 46 of them, back
    to ACE.NZ from ten days ago. Showing them all buried the four tickers
    actually running behind 42 finished ones, and printed idle times like
    "14488m55" which say nothing useful.
    """

    def test_a_long_idle_ticker_is_excluded_by_default(self, tmp_path):
        write_log(tmp_path, "OLD.NZ", tool("Bash"),
                  mtime=time.time() - 10 * 86400)
        write_log(tmp_path, "NOW.NZ", tool("Bash"))
        out = run_status.render(tmp_path)
        assert "NOW.NZ" in out
        assert "OLD.NZ" not in out

    def test_since_zero_shows_everything(self, tmp_path):
        write_log(tmp_path, "OLD.NZ", tool("Bash"),
                  mtime=time.time() - 10 * 86400)
        assert "OLD.NZ" in run_status.render(tmp_path, since=0)

    def test_a_ticker_in_the_joblog_is_kept_however_old(self, tmp_path):
        # It is part of this run by definition, even if it finished early.
        write_log(tmp_path, "EARLY.NZ", tool("Bash"), result(cost=1.0),
                  mtime=time.time() - 10 * 86400)
        write_joblog(tmp_path, (1, 100, 0, "EARLY.NZ"))
        out = run_status.render(tmp_path,
                                joblog=tmp_path / "state" / "joblog.tsv")
        assert "EARLY.NZ" in out

    def test_idle_is_shown_in_hours_once_it_passes_an_hour(self, tmp_path):
        assert run_status._fmt_secs(9000) == "2h30m"
        assert run_status._fmt_secs(600) == "10m00"


class TestRender:
    def test_the_table_has_a_row_per_ticker(self, tmp_path):
        write_log(tmp_path, "A.NZ", tool("Bash", description="one"))
        write_log(tmp_path, "B.NZ", tool("Read", file_path="x"),
                  result(cost=2.5))
        out = run_status.render(tmp_path)
        assert "A.NZ" in out
        assert "B.NZ" in out

    def test_cost_total_is_shown(self, tmp_path):
        # The number that was invisible until a run finished.
        write_log(tmp_path, "A.NZ", tool("Bash"), result(cost=16.99))
        write_log(tmp_path, "B.NZ", tool("Bash"), result(cost=12.86))
        assert "29.85" in run_status.render(tmp_path)

    def test_an_empty_run_says_so(self, tmp_path):
        assert "no run" in run_status.render(tmp_path).lower()

    def test_long_tool_text_is_truncated_to_keep_columns_aligned(self,
                                                                 tmp_path):
        write_log(tmp_path, "A.NZ", tool("Bash", description="x" * 300))
        for line in run_status.render(tmp_path).splitlines():
            assert len(line) < 200


class TestCli:
    def _run(self, monkeypatch, capsys, tmp_path, *argv):
        monkeypatch.setattr(sys, "argv", ["run_status.py", "--root",
                                          str(tmp_path), *argv])
        assert run_status.main() == 0
        return capsys.readouterr().out

    def test_prints_once_and_exits(self, tmp_path, monkeypatch, capsys):
        write_log(tmp_path, "A.NZ", tool("Bash"), result(cost=1.0))
        assert "A.NZ" in self._run(monkeypatch, capsys, tmp_path)

    def test_watch_with_one_iteration_prints_once(self, tmp_path, monkeypatch,
                                                  capsys):
        # --once keeps --watch testable without a sleep loop.
        write_log(tmp_path, "A.NZ", tool("Bash"))
        out = self._run(monkeypatch, capsys, tmp_path, "--watch", "--once")
        assert "A.NZ" in out

    def test_watch_redraws_in_place(self, tmp_path, monkeypatch, capsys):
        # Cursor-home + clear-below, so the table refreshes without the
        # flicker of a full screen clear.
        write_log(tmp_path, "A.NZ", tool("Bash"))
        out = self._run(monkeypatch, capsys, tmp_path, "--watch", "--once")
        assert out.startswith("\x1b[H\x1b[J")

    def test_watch_loops_until_interrupted(self, tmp_path, monkeypatch,
                                           capsys):
        # Without --once it redraws on a timer; a Ctrl-C during the sleep is
        # a clean exit, not a traceback in the status pane.
        write_log(tmp_path, "A.NZ", tool("Bash"))
        calls = []

        def fake_sleep(seconds):
            calls.append(seconds)
            if len(calls) >= 2:
                raise KeyboardInterrupt
        monkeypatch.setattr(run_status.time, "sleep", fake_sleep)
        monkeypatch.setattr(sys, "argv",
                            ["run_status.py", "--root", str(tmp_path),
                             "--watch", "--interval", "0.01"])
        assert run_status.main() == 0
        assert len(calls) == 2
        assert capsys.readouterr().out.count("A.NZ") == 2

    def test_since_flag_reaches_render(self, tmp_path, monkeypatch, capsys):
        write_log(tmp_path, "OLD.NZ", tool("Bash"),
                  mtime=time.time() - 10 * 86400)
        assert "OLD.NZ" in self._run(monkeypatch, capsys, tmp_path,
                                     "--since", "0")
