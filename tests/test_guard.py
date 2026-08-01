"""Tests for guard.py: the weekend/budget gate for the CI screener."""

import datetime as dt
import json
import sys

import guard


def run_guard(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["guard.py", *argv])
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    return guard.main()


def budget(key=None):
    state = json.loads(guard.STATE_FILE.read_text())
    return state if key is None else state.get(key)


class TestWeekendKey:
    def test_saturday_and_sunday_share_a_key(self):
        sat = dt.date(2026, 8, 1)
        sun = dt.date(2026, 8, 2)
        assert guard.weekend_key(sat) == guard.weekend_key(sun) == "2026-W31"

    def test_iso_year_boundary(self):
        # 2027-01-02/03 fall in ISO week 2026-W53: the key must use the ISO
        # year, or the first weekend of January would collide with W01.
        sat = dt.date(2027, 1, 2)
        sun = dt.date(2027, 1, 3)
        assert sat.isocalendar()[0] == 2026
        assert guard.weekend_key(sat) == guard.weekend_key(sun) == "2026-W53"


class TestMain:
    def test_weekday_blocked_without_consuming_budget(self, patch_repo, monkeypatch, capsys):
        assert run_guard(monkeypatch, "--today", "2026-08-05") == 0  # Wednesday
        assert "proceed=false" in capsys.readouterr().out
        assert not guard.STATE_FILE.exists()

    def test_weekend_run_claims_a_slot(self, patch_repo, monkeypatch, capsys):
        assert run_guard(monkeypatch, "--today", "2026-08-01") == 0  # Saturday
        assert "proceed=true" in capsys.readouterr().out
        assert budget("2026-W31") == 1

    def test_budget_exhaustion_blocks(self, patch_repo, monkeypatch, capsys):
        for _ in range(2):
            run_guard(monkeypatch, "--today", "2026-08-01", "--max-runs", "2")
        capsys.readouterr()
        assert run_guard(monkeypatch, "--today", "2026-08-02", "--max-runs", "2") == 0
        assert "proceed=false" in capsys.readouterr().out
        assert budget("2026-W31") == 2   # blocked run does not increment

    def test_slot_claimed_before_research(self, patch_repo, monkeypatch):
        # The increment happens inside main(), before any expensive step
        # runs: a crash after guard still costs one run, so a persistently
        # failing ticker cannot drain the budget by retrying every tick.
        run_guard(monkeypatch, "--today", "2026-08-01")
        assert budget("2026-W31") == 1

    def test_ignore_budget_neither_checks_nor_increments(self, patch_repo, monkeypatch, capsys):
        assert run_guard(monkeypatch, "--today", "2026-08-01", "--ignore-budget") == 0
        assert "proceed=true" in capsys.readouterr().out
        assert not guard.STATE_FILE.exists()

    def test_ignore_weekend_still_applies_budget(self, patch_repo, monkeypatch, capsys):
        assert run_guard(monkeypatch, "--today", "2026-08-05",
                         "--ignore-weekend", "--max-runs", "1") == 0
        assert "proceed=true" in capsys.readouterr().out
        assert run_guard(monkeypatch, "--today", "2026-08-05",
                         "--ignore-weekend", "--max-runs", "1") == 0
        assert "proceed=false" in capsys.readouterr().out
