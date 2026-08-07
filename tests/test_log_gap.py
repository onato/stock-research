"""log_gap.py: the gap log must support closure without losing history.

The file is append-only by design (agents write, never edit). Resolution
therefore appends a {"resolves": <line_no>, "note": ...} record rather than
editing the original entry; --report and --list hide resolved entries by
default, so `make gaps` shows the open backlog instead of everything ever
observed (WISE.L kept showing 9 entries after its research was fixed).
"""

import sys

import log_gap
import pytest


@pytest.fixture
def gap_log(monkeypatch, tmp_path):
    log = tmp_path / "improvements.jsonl"
    monkeypatch.setattr(log_gap, "LOG", log)
    return log


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["log_gap.py", *argv])
    return log_gap.main()


def add_entry(monkeypatch, ticker="WISE.L", metric="EBITDA"):
    return run(monkeypatch, "--ticker", ticker, "--kind", "missing_pattern",
               "--metric", metric, "--detail", "wording not matched")


class TestResolve:
    def test_resolved_entry_hidden_from_report(self, gap_log, monkeypatch, capsys):
        add_entry(monkeypatch)
        assert run(monkeypatch, "--resolve", "1", "--note", "pattern added") == 0
        capsys.readouterr()
        run(monkeypatch, "--report")
        out = capsys.readouterr().out
        assert "no gaps logged yet" in out or "0 observation" in out or \
               "no matching entries" in out or "no open" in out.lower()

    def test_resolution_appends_never_edits(self, gap_log, monkeypatch):
        add_entry(monkeypatch)
        original = gap_log.read_text()
        run(monkeypatch, "--resolve", "1", "--note", "fixed")
        after = gap_log.read_text()
        assert after.startswith(original)          # original line untouched
        assert len(after.splitlines()) == 2        # resolution appended

    def test_open_entries_still_reported(self, gap_log, monkeypatch, capsys):
        add_entry(monkeypatch, metric="EBITDA")
        add_entry(monkeypatch, ticker="AFC.NZ", metric="CapEx")
        run(monkeypatch, "--resolve", "1", "--note", "fixed")
        capsys.readouterr()
        run(monkeypatch, "--report")
        out = capsys.readouterr().out
        assert "CapEx" in out
        assert "EBITDA" not in out
        assert "1 resolved" in out

    def test_resolve_unknown_line_errors(self, gap_log, monkeypatch, capsys):
        add_entry(monkeypatch)
        assert run(monkeypatch, "--resolve", "99", "--note", "x") != 0

    def test_resolve_already_resolved_errors(self, gap_log, monkeypatch, capsys):
        add_entry(monkeypatch)
        run(monkeypatch, "--resolve", "1", "--note", "first")
        assert run(monkeypatch, "--resolve", "1", "--note", "again") != 0


class TestList:
    def test_list_numbers_open_entries_by_line(self, gap_log, monkeypatch, capsys):
        add_entry(monkeypatch, metric="EBITDA")
        add_entry(monkeypatch, metric="CapEx")
        run(monkeypatch, "--resolve", "1", "--note", "fixed")
        capsys.readouterr()
        run(monkeypatch, "--list")
        out = capsys.readouterr().out
        assert "#2" in out
        assert "CapEx" in out
        assert "EBITDA" not in out

    def test_list_all_includes_resolved(self, gap_log, monkeypatch, capsys):
        add_entry(monkeypatch, metric="EBITDA")
        run(monkeypatch, "--resolve", "1", "--note", "pattern added")
        capsys.readouterr()
        run(monkeypatch, "--list", "--all")
        out = capsys.readouterr().out
        assert "EBITDA" in out
        assert "pattern added" in out
