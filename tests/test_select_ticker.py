"""Tests for select_ticker.py: queue ordering, new/stale selection, emit/main.

select_ticker binds REPO_ROOT and QUEUE_DIR at import time, so every test
retargets those module attributes at a tmp_path tree — the live queue/ and
research/ directories are never read. GITHUB_OUTPUT is cleared so a CI
environment cannot leak into emit().
"""

import datetime as dt
import importlib.util
import json
import runpy
import sys
import types
import warnings
from pathlib import Path

import pytest
import select_ticker as st


@pytest.fixture
def st_repo(monkeypatch, tmp_path):
    """Retarget select_ticker's import-time paths at a tmp repo skeleton."""
    monkeypatch.setattr(st, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(st, "QUEUE_DIR", tmp_path / "queue")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    (tmp_path / "queue").mkdir()
    (tmp_path / "research").mkdir()
    return tmp_path


def write_queue(repo, name, tickers):
    (repo / "queue" / name).write_text("\n".join(tickers) + "\n")


def give_reports(repo, ticker, files=("T_Metrics.csv",)):
    d = repo / "research" / ticker / "Reports"
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / f).write_text("data")
    return d


def give_dcf(repo, ticker, valuation_date=None, text=None):
    d = give_reports(repo, ticker, files=())
    if text is None:
        text = json.dumps({"valuation_date": valuation_date})
    (d / f"{ticker}_DCF.json").write_text(text)
    return d


def fresh_import(name):
    """Execute scripts/select_ticker.py as a new module object."""
    spec = importlib.util.spec_from_file_location(name, Path(st.__file__))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParseDateImport:
    def test_uses_screen_parse_date_when_importable(self, monkeypatch):
        """The skill's parse_date wins so staleness means the same thing in
        the selector as it does in the screener."""
        fake = types.SimpleNamespace(parse_date=lambda s: "FROM-SCREEN")
        monkeypatch.setitem(sys.modules, "screen", fake)
        mod = fresh_import("select_ticker_screen_branch")
        assert mod.parse_date("anything") == "FROM-SCREEN"

    def test_fallback_when_screen_unimportable(self, monkeypatch):
        # None in sys.modules makes `from screen import ...` raise
        # ImportError, forcing the standalone fallback definition.
        monkeypatch.setitem(sys.modules, "screen", None)
        mod = fresh_import("select_ticker_fallback_branch")
        assert mod.parse_date("2026-03-15") == dt.date(2026, 3, 15)
        assert mod.parse_date("2026/03/15") == dt.date(2026, 3, 15)
        assert mod.parse_date("2026-03") == dt.date(2026, 3, 1)
        assert mod.parse_date("2026-03-15T00:00:00") == dt.date(2026, 3, 15)
        assert mod.parse_date(None) is None
        assert mod.parse_date("") is None
        assert mod.parse_date("not a date") is None


class TestQueueFiles:
    def test_priority_order_then_alphabetical_extras(self, st_repo):
        """Named files come in PRIORITY order; unknown files append sorted,
        so dropping in a new exchange list needs no code edit."""
        for name in ("zzz_new.txt", "asx.txt", "priority.txt",
                     "aaa_new.txt", "nzx.txt"):
            write_queue(st_repo, name, ["X"])
        assert [p.name for p in st.queue_files()] == [
            "priority.txt", "nzx.txt", "asx.txt", "aaa_new.txt", "zzz_new.txt"]

    def test_missing_queue_dir_is_empty(self, st_repo, monkeypatch):
        monkeypatch.setattr(st, "QUEUE_DIR", st_repo / "no_such_dir")
        assert st.queue_files() == []

    def test_non_txt_files_ignored(self, st_repo):
        (st_repo / "queue" / "notes.md").write_text("AAA\n")
        write_queue(st_repo, "nzx.txt", ["BBB.NZ"])
        assert [p.name for p in st.queue_files()] == ["nzx.txt"]


class TestReadTickers:
    def test_strips_comments_and_blanks(self, st_repo):
        (st_repo / "queue" / "q.txt").write_text(
            "# full-line comment\n"
            "AAA.NZ\n"
            "\n"
            "  BBB.NZ  # inline comment\n"
            "   \n")
        assert st.read_tickers(st_repo / "queue" / "q.txt") == ["AAA.NZ", "BBB.NZ"]


class TestHasReports:
    def test_no_directory(self, st_repo):
        assert not st.has_reports("GHOST")

    def test_empty_reports_dir_still_eligible(self, st_repo):
        """An empty Reports/ means a previous run died partway; the ticker
        must stay pickable rather than being skipped forever."""
        give_reports(st_repo, "HALF", files=())
        assert not st.has_reports("HALF")

    def test_any_content_counts(self, st_repo):
        give_reports(st_repo, "DONE")
        assert st.has_reports("DONE")


class TestPickNew:
    def test_first_unresearched_in_priority_order(self, st_repo):
        write_queue(st_repo, "nzx.txt", ["C.NZ"])
        write_queue(st_repo, "priority.txt", ["A", "B"])
        give_reports(st_repo, "A")
        assert st.pick_new() == "B"

    def test_exclude_skips_reserved_names(self, st_repo):
        write_queue(st_repo, "priority.txt", ["A", "B"])
        assert st.pick_new(exclude={"A"}) == "B"

    def test_all_researched_returns_none(self, st_repo):
        write_queue(st_repo, "priority.txt", ["A"])
        give_reports(st_repo, "A")
        assert st.pick_new() is None

    def test_empty_queue_returns_none(self, st_repo):
        assert st.pick_new() is None


class TestPickStalest:
    def test_oldest_valuation_date_wins(self, st_repo):
        give_dcf(st_repo, "NEW", "2026-08-01")
        give_dcf(st_repo, "OLD", "2025-01-01")
        assert st.pick_stalest() == "OLD"

    def test_missing_date_sorts_before_any_dated(self, st_repo):
        """No/unparseable valuation_date reads as 'refresh soonest'."""
        give_dcf(st_repo, "DATED", "2020-01-01")
        give_dcf(st_repo, "NODATE", None)
        assert st.pick_stalest() == "NODATE"

    def test_corrupt_json_sorts_first(self, st_repo):
        give_dcf(st_repo, "DATED", "2020-01-01")
        give_dcf(st_repo, "BROKEN", text="{not json")
        assert st.pick_stalest() == "BROKEN"

    def test_date_tie_breaks_alphabetically(self, st_repo):
        give_dcf(st_repo, "ZED", "2026-01-01")
        give_dcf(st_repo, "ANN", "2026-01-01")
        assert st.pick_stalest() == "ANN"

    def test_exclude(self, st_repo):
        give_dcf(st_repo, "OLD", "2025-01-01")
        give_dcf(st_repo, "NEW", "2026-08-01")
        assert st.pick_stalest(exclude={"OLD"}) == "NEW"

    def test_no_dcfs_returns_none(self, st_repo):
        assert st.pick_stalest() is None


class TestEmit:
    def test_writes_github_output_and_stdout(self, st_repo, monkeypatch,
                                             tmp_path, capsys):
        out = tmp_path / "gh_output"
        out.write_text("existing=1\n")
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))
        st.emit("WISE.L", "new")
        # appended, not truncated: other workflow steps share the file
        assert out.read_text() == "existing=1\nticker=WISE.L\nmode=new\n"
        assert capsys.readouterr().out == "ticker=WISE.L\nmode=new\n"

    def test_without_env_only_prints(self, st_repo, capsys):
        st.emit(None, "none")
        assert capsys.readouterr().out == "ticker=\nmode=none\n"


class TestMain:
    def run(self, monkeypatch, *argv):
        monkeypatch.setattr(sys, "argv", ["select_ticker.py", *argv])
        return st.main()

    def test_override_new(self, st_repo, monkeypatch, capsys):
        assert self.run(monkeypatch, "--override", "FRESH") == 0
        cap = capsys.readouterr()
        assert cap.out == "ticker=FRESH\nmode=new\n"
        assert "Override: FRESH (new)" in cap.err

    def test_override_refresh_when_reports_exist(self, st_repo, monkeypatch,
                                                 capsys):
        give_reports(st_repo, "DONE")
        assert self.run(monkeypatch, "--override", "DONE") == 0
        assert capsys.readouterr().out == "ticker=DONE\nmode=refresh\n"

    def test_new_ticker_selected_first(self, st_repo, monkeypatch, capsys):
        write_queue(st_repo, "priority.txt", ["A"])
        give_dcf(st_repo, "STALE", "2020-01-01")
        assert self.run(monkeypatch) == 0
        assert capsys.readouterr().out == "ticker=A\nmode=new\n"

    def test_exhausted_queue_falls_back_to_stalest(self, st_repo, monkeypatch,
                                                   capsys):
        write_queue(st_repo, "priority.txt", ["A"])
        give_reports(st_repo, "A")
        give_dcf(st_repo, "A", "2026-01-01")
        give_dcf(st_repo, "STALE", "2020-01-01")
        assert self.run(monkeypatch) == 0
        assert capsys.readouterr().out == "ticker=STALE\nmode=refresh\n"

    def test_exclude_flag_reserves_batch(self, st_repo, monkeypatch, capsys):
        write_queue(st_repo, "priority.txt", ["A", "B"])
        assert self.run(monkeypatch, "--exclude", "A, ,B") == 0
        # both excluded and nothing else exists -> clean no-op
        assert capsys.readouterr().out == "ticker=\nmode=none\n"

    def test_nothing_to_do_is_clean_noop(self, st_repo, monkeypatch, capsys):
        """Empty ticker= with exit 0: the workflow treats it as a no-op,
        never a failure."""
        assert self.run(monkeypatch) == 0
        cap = capsys.readouterr()
        assert cap.out == "ticker=\nmode=none\n"
        assert "Nothing to do" in cap.err

    def test_entrypoint_exits_with_main_status(self, st_repo, monkeypatch,
                                               capsys):
        # runpy executes a FRESH module bound to the real repo paths, so use
        # --override: it short-circuits before any queue/research scan (the
        # only touch is a stat of a research dir that cannot exist).
        monkeypatch.setattr(sys, "argv", ["select_ticker.py",
                                          "--override", "NO.SUCH.TICKER"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(SystemExit) as ei:
                runpy.run_module("select_ticker", run_name="__main__")
        assert ei.value.code == 0
        assert capsys.readouterr().out == "ticker=NO.SUCH.TICKER\nmode=new\n"


class TestBatch:
    """`--count N` returns N tickers from one process.

    run_loop built its queue by calling this script once per ticker, each
    spawning `uv run python3`. With no -n that meant enumerating all 1748
    unresearched tickers -- about two minutes of silence before the first
    line of output, which reads as a hang.
    """

    def run(self, monkeypatch, *argv):
        monkeypatch.setattr(sys, "argv", ["select_ticker.py", *argv])
        return st.main()

    def test_count_returns_several_tickers(self, st_repo, monkeypatch, capsys):
        write_queue(st_repo, "priority.txt", ["A", "B", "C"])
        assert self.run(monkeypatch, "--count", "3") == 0
        assert capsys.readouterr().out == "ticker=A\nticker=B\nticker=C\nmode=new\n"

    def test_count_never_repeats_a_ticker(self, st_repo, monkeypatch, capsys):
        # Asking for more than the queue holds must not loop on the last
        # pick: each reservation has to exclude the ones already handed out.
        write_queue(st_repo, "priority.txt", ["A", "B"])
        assert self.run(monkeypatch, "--count", "5") == 0
        lines = capsys.readouterr().out.splitlines()
        picked = [ln.split("=", 1)[1] for ln in lines
                  if ln.startswith("ticker=")]
        assert picked == ["A", "B"]          # stops when the queue runs dry

    def test_count_stops_at_the_queue_end(self, st_repo, monkeypatch, capsys):
        write_queue(st_repo, "priority.txt", ["A"])
        assert self.run(monkeypatch, "--count", "10") == 0
        picked = [ln for ln in capsys.readouterr().out.splitlines()
                  if ln.startswith("ticker=")]
        assert picked == ["ticker=A"]

    def test_count_falls_back_to_stale_refreshes(self, st_repo, monkeypatch,
                                                 capsys):
        write_queue(st_repo, "priority.txt", ["A"])
        give_reports(st_repo, "A")
        give_dcf(st_repo, "A", "2026-01-01")
        give_dcf(st_repo, "OLD", "2020-01-01")
        assert self.run(monkeypatch, "--count", "2") == 0
        picked = [ln.split("=", 1)[1] for ln in
                  capsys.readouterr().out.splitlines()
                  if ln.startswith("ticker=")]
        assert "OLD" in picked

    def test_count_honours_exclude(self, st_repo, monkeypatch, capsys):
        write_queue(st_repo, "priority.txt", ["A", "B", "C"])
        assert self.run(monkeypatch, "--count", "3", "--exclude", "B") == 0
        picked = [ln.split("=", 1)[1] for ln in
                  capsys.readouterr().out.splitlines()
                  if ln.startswith("ticker=")]
        assert picked == ["A", "C"]

    def test_count_one_matches_the_single_ticker_form(self, st_repo,
                                                      monkeypatch, capsys):
        write_queue(st_repo, "priority.txt", ["A"])
        assert self.run(monkeypatch, "--count", "1") == 0
        assert capsys.readouterr().out == "ticker=A\nmode=new\n"

    def test_empty_queue_is_a_clean_noop(self, st_repo, monkeypatch, capsys):
        assert self.run(monkeypatch, "--count", "5") == 0
        assert capsys.readouterr().out == "ticker=\nmode=none\n"
