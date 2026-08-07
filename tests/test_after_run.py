"""Tests for after_run.py: the post-run digest's decision logic.

What matters is which tickers the digest picks up, which checks it flags,
and how it ranks the suggested next actions — eval FAILs first, systemic
warns second, extractor gaps last. Print formatting is incidental; the
assertions target substrings that carry the decision, not layout.
"""

import json
import os
import subprocess
import types

import after_run
import pytest


@pytest.fixture
def digest_repo(monkeypatch, tmp_path):
    """Retarget after_run's import-time repo paths at a tmp skeleton.

    after_run binds REPO/SCORES/JOBLOG at import, so hermetic tests must
    patch the module attributes (same rule as conftest's patch_repo).
    """
    (tmp_path / "state" / "scores").mkdir(parents=True)
    monkeypatch.setattr(after_run, "REPO", tmp_path)
    monkeypatch.setattr(after_run, "SCORES", tmp_path / "state" / "scores")
    monkeypatch.setattr(after_run, "JOBLOG", tmp_path / "state" / "joblog.tsv")
    return tmp_path


@pytest.fixture
def canned_run(monkeypatch):
    """Stub after_run.run so main() never spawns subprocesses.

    Returns a dict keyed by script basename; tests fill in canned stdout
    for cost_report.py / log_gap.py.
    """
    outputs = {"cost_report.py": "", "log_gap.py": ""}

    def fake_run(cmd):
        for key, val in outputs.items():
            if any(key in part for part in cmd):
                return val
        return ""

    monkeypatch.setattr(after_run, "run", fake_run)
    return outputs


def write_joblog(repo, commands):
    """A GNU-parallel-style joblog: header line, then command in last column."""
    lines = ["Seq\tHost\tStarttime\tCommand"]
    lines += [f"{i}\t:\t170000000{i}\t{cmd}" for i, cmd in enumerate(commands, 1)]
    (repo / "state" / "joblog.tsv").write_text("\n".join(lines) + "\n")


def write_scorecard(repo, ticker, checks, name="20260101"):
    (repo / "state" / "scores" / f"{ticker}_{name}.json").write_text(
        json.dumps({"checks": checks, "agents_sha": "abc123def456789"}))


class TestLastBatch:
    def test_tickers_come_from_joblog_command_column(self, digest_repo):
        # The joblog's last column is the whole command, not the ticker;
        # last_batch must take the final whitespace token.
        write_joblog(digest_repo, [
            "/repo/scripts/research_one.sh WISE.L",
            "/repo/scripts/research_one.sh PYPL",
        ])
        assert after_run.last_batch() == ["WISE.L", "PYPL"]

    def test_header_line_is_skipped(self, digest_repo):
        # "Command" in the header has no slash, so it would be mistaken for
        # a ticker if the first line weren't dropped.
        write_joblog(digest_repo, ["/repo/scripts/research_one.sh SEK.NZ"])
        assert "Command" not in after_run.last_batch()

    def test_bare_path_commands_are_dropped(self, digest_repo):
        # A command with no ticker argument ends in a path; "/" marks it
        # as not-a-ticker.
        write_joblog(digest_repo, [
            "/repo/scripts/warmup.sh",
            "/repo/scripts/research_one.sh DUOL",
        ])
        assert after_run.last_batch() == ["DUOL"]

    def test_short_or_blank_rows_are_skipped(self, digest_repo):
        (digest_repo / "state" / "joblog.tsv").write_text(
            "Seq\tCommand\n"
            "loneField\n"            # fewer than 2 columns
            "2\t   \n"               # blank command column
            "3\t/x/research_one.sh AMZN\n")
        assert after_run.last_batch() == ["AMZN"]

    def test_falls_back_to_recent_log_stems_without_joblog(self, digest_repo):
        # No joblog at all (run_local.sh writes none): use the six most
        # recently written transcripts, newest first.
        logs = digest_repo / "state" / "logs"
        logs.mkdir()
        for i, name in enumerate(["T0", "T1", "T2", "T3", "T4", "T5", "T6"]):
            p = logs / f"{name}.log"
            p.write_text("x")
            os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
        batch = after_run.last_batch()
        assert batch == ["T6", "T5", "T4", "T3", "T2", "T1"]

    def test_empty_joblog_body_falls_back_to_logs(self, digest_repo):
        write_joblog(digest_repo, [])
        logs = digest_repo / "state" / "logs"
        logs.mkdir()
        (logs / "NFLX.log").write_text("x")
        assert after_run.last_batch() == ["NFLX"]

    def test_nothing_anywhere_yields_empty(self, digest_repo):
        assert after_run.last_batch() == []


class TestLatestScorecard:
    def test_missing_scorecard_is_none(self, digest_repo):
        assert after_run.latest_scorecard("WISE.L") is None

    def test_picks_lexically_last_card(self, digest_repo):
        write_scorecard(digest_repo, "DUOL", [], name="20260101")
        write_scorecard(digest_repo, "DUOL", [{"id": "newer", "status": "ok",
                                               "detail": ""}], name="20260615")
        card = after_run.latest_scorecard("DUOL")
        assert card["checks"][0]["id"] == "newer"

    def test_other_tickers_cards_are_not_matched(self, digest_repo):
        write_scorecard(digest_repo, "PYPL", [])
        assert after_run.latest_scorecard("P") is None

    def test_malformed_json_is_none_not_a_crash(self, digest_repo):
        (digest_repo / "state" / "scores" / "AMZN_1.json").write_text("{nope")
        assert after_run.latest_scorecard("AMZN") is None


class TestRunWrapper:
    def test_returns_stdout(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(stdout="cost lines\n"))
        assert after_run.run(["python3", "x.py"]) == "cost lines\n"

    def test_failure_becomes_inline_marker_not_exception(self, monkeypatch):
        # The digest is read-only reporting; a broken helper script must
        # degrade to a "(failed: ...)" note, never abort the digest.
        def boom(*a, **k):
            raise OSError("no such file")

        monkeypatch.setattr(subprocess, "run", boom)
        assert after_run.run(["nope"]).startswith("(failed:")


class TestMain:
    def test_no_tickers_exits_1(self, digest_repo, canned_run, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["after_run.py"])
        assert after_run.main() == 1
        assert "no tickers" in capsys.readouterr().out

    def test_clean_batch_flags_nothing(self, digest_repo, canned_run,
                                       monkeypatch, capsys):
        write_scorecard(digest_repo, "WISE.L",
                        [{"id": "csv_headers", "status": "ok", "detail": "fine"}])
        monkeypatch.setattr("sys.argv", ["after_run.py", "WISE.L"])
        assert after_run.main() == 0
        out = capsys.readouterr().out
        assert "clean" in out
        assert "Nothing flagged. Clean batch." in out

    def test_fail_outranks_ok_and_missing_scorecard_is_reported(
            self, digest_repo, canned_run, monkeypatch, capsys):
        """A failing check flags the ticker FAIL and lands a FIX action;
        a ticker with no scorecard is called out rather than skipped."""
        write_scorecard(digest_repo, "DUOL",
                        [{"id": "dcf_entry_price", "status": "fail",
                          "detail": "entry price above intrinsic value"}])
        monkeypatch.setattr("sys.argv", ["after_run.py", "DUOL", "GHOST"])
        assert after_run.main() == 0
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "no scorecard" in out
        assert "FIX DUOL: dcf_entry_price" in out

    def test_warn_on_one_of_two_tickers_is_not_systemic(
            self, digest_repo, canned_run, monkeypatch, capsys):
        # Threshold is max(2, n//2): a single warn is a quirk, not a pattern.
        write_scorecard(digest_repo, "A1",
                        [{"id": "units_null", "status": "warn", "detail": "d"}])
        write_scorecard(digest_repo, "B2", [])
        monkeypatch.setattr("sys.argv", ["after_run.py", "A1", "B2"])
        after_run.main()
        assert "likely systemic" not in capsys.readouterr().out

    def test_same_warn_on_both_tickers_is_systemic(
            self, digest_repo, canned_run, monkeypatch, capsys):
        warn = {"id": "units_null", "status": "warn", "detail": "d"}
        write_scorecard(digest_repo, "A1", [warn])
        write_scorecard(digest_repo, "B2", [warn])
        monkeypatch.setattr("sys.argv", ["after_run.py", "A1", "B2"])
        after_run.main()
        out = capsys.readouterr().out
        assert "units_null warns on 2/2 tickers" in out
        assert "likely systemic" in out

    def test_action_ranking_fix_then_systemic_then_gaps(
            self, digest_repo, canned_run, monkeypatch, capsys):
        """Ranked list order: eval FAILs (wrong output) first, systemic
        warns second, missing extractor patterns last."""
        canned_run["log_gap.py"] = (
            "improvements log: 3 entries\n"
            "\n"
            "by metric:\n"
            "  revenue   2\n"
            "  npat      1\n"
            "\n"
            "by ticker:\n"
            "  WISE.L    3\n")
        warn = {"id": "units_null", "status": "warn", "detail": "d"}
        write_scorecard(digest_repo, "A1",
                        [warn, {"id": "dcf_entry_price", "status": "fail",
                                "detail": "bad"}])
        write_scorecard(digest_repo, "B2", [warn])
        monkeypatch.setattr("sys.argv", ["after_run.py", "A1", "B2"])
        after_run.main()
        out = capsys.readouterr().out
        i_fix = out.index("FIX A1: dcf_entry_price")
        i_sys = out.index("likely systemic")
        i_gap = out.index("missing 2 pattern(s)")
        assert i_fix < i_sys < i_gap

    def test_gap_section_stops_at_blank_line(
            self, digest_repo, canned_run, monkeypatch, capsys):
        # Only the "by metric" block counts as missing patterns; the
        # "by ticker" block after the blank line must not inflate it.
        canned_run["log_gap.py"] = (
            "by metric:\n"
            "  revenue   4\n"
            "\n"
            "by ticker:\n"
            "  A1  4\n")
        write_scorecard(digest_repo, "A1", [])
        monkeypatch.setattr("sys.argv", ["after_run.py", "A1"])
        after_run.main()
        out = capsys.readouterr().out
        assert "missing 1 pattern(s)" in out
        assert "Missing extractor patterns" in out
        assert "revenue   4" in out

    def test_cost_report_output_is_echoed(self, digest_repo, canned_run,
                                          monkeypatch, capsys):
        canned_run["cost_report.py"] = "total: $12.34\n"
        write_scorecard(digest_repo, "A1", [])
        monkeypatch.setattr("sys.argv", ["after_run.py", "A1"])
        after_run.main()
        assert "total: $12.34" in capsys.readouterr().out
