"""filter_tickers.py decides which of a supplied list still needs research.

run_loop.sh applies select_ticker's policy -- new tickers first, then the
stalest -- only when invoked with no arguments. Passing tickers explicitly
bypassed every filter, so `run_loop.sh -n 20 $(cat state/backlog.txt)` re-ran
six already-finished tickers (AIA.NZ, ALF.NZ, ANZ.NZ ...), ignored -n, and
queued all 782 entries including a prose GAP note that word-splitting turned
into fake tickers.

This is the shared filter both paths use, so an explicit list gets the same
treatment the queue does.
"""

import datetime as dt
import json

import filter_tickers
import pytest


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "research").mkdir()
    return tmp_path


def researched(repo, ticker, days_old=0):
    """A ticker with a DCF dated `days_old` days ago."""
    d = repo / "research" / ticker / "Reports"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ticker}_Metrics.csv").write_text("Period\nFY2024\n")
    when = dt.date.today() - dt.timedelta(days=days_old)
    (d / f"{ticker}_DCF.json").write_text(
        json.dumps({"valuation_date": when.isoformat()}))


class TestShape:
    def test_a_non_ticker_line_is_dropped(self, repo):
        # backlog.txt carries a prose note; $(cat) splits it into words that
        # would otherwise be scheduled as tickers.
        got = filter_tickers.eligible(
            repo, ["AAA.NZ", "GAP:", "dashboard-generator", "over-escaping"])
        assert got == ["AAA.NZ"]

    def test_common_ticker_shapes_survive(self, repo):
        supplied = ["AIA.NZ", "0700.HK", "WISE.L", "NFLX", "FLOW.AS", "FIH.U"]
        assert filter_tickers.eligible(repo, supplied) == supplied

    def test_duplicates_collapse_keeping_order(self, repo):
        got = filter_tickers.eligible(repo, ["BBB.NZ", "AAA.NZ", "BBB.NZ"])
        assert got == ["BBB.NZ", "AAA.NZ"]

    def test_blank_entries_are_ignored(self, repo):
        assert filter_tickers.eligible(repo, ["", "  ", "AAA.NZ"]) == ["AAA.NZ"]


class TestSkipping:
    def test_an_unresearched_ticker_is_eligible(self, repo):
        assert filter_tickers.eligible(repo, ["NEW.NZ"]) == ["NEW.NZ"]

    def test_a_freshly_researched_ticker_is_skipped(self, repo):
        researched(repo, "ALF.NZ", days_old=0)
        assert filter_tickers.eligible(repo, ["ALF.NZ", "NEW.NZ"]) == ["NEW.NZ"]

    def test_a_stale_ticker_is_eligible_again(self, repo):
        # The point of a refresh pass: old research is work, not done work.
        researched(repo, "OLD.NZ", days_old=120)
        assert filter_tickers.eligible(repo, ["OLD.NZ"],
                                       stale_days=45) == ["OLD.NZ"]

    def test_the_staleness_boundary(self, repo):
        researched(repo, "EDGE.NZ", days_old=45)
        assert filter_tickers.eligible(repo, ["EDGE.NZ"], stale_days=45) == \
            ["EDGE.NZ"]
        researched(repo, "YOUNG.NZ", days_old=44)
        assert filter_tickers.eligible(repo, ["YOUNG.NZ"], stale_days=45) == []

    def test_an_empty_reports_dir_stays_eligible(self, repo):
        # A previous run died partway; it must not be skipped forever.
        (repo / "research" / "DIED.NZ" / "Reports").mkdir(parents=True)
        assert filter_tickers.eligible(repo, ["DIED.NZ"]) == ["DIED.NZ"]

    def test_a_researched_ticker_without_a_dcf_is_eligible(self, repo):
        # Partial output -- metrics but no valuation -- is unfinished work.
        d = repo / "research" / "PART.NZ" / "Reports"
        d.mkdir(parents=True)
        (d / "PART.NZ_Metrics.csv").write_text("Period\nFY2024\n")
        assert filter_tickers.eligible(repo, ["PART.NZ"]) == ["PART.NZ"]

    def test_an_unparseable_dcf_date_counts_as_stale(self, repo):
        d = repo / "research" / "ODD.NZ" / "Reports"
        d.mkdir(parents=True)
        (d / "ODD.NZ_Metrics.csv").write_text("Period\nFY2024\n")
        (d / "ODD.NZ_DCF.json").write_text('{"valuation_date": "not a date"}')
        assert filter_tickers.eligible(repo, ["ODD.NZ"]) == ["ODD.NZ"]

    def test_force_keeps_everything(self, repo):
        # Explicitly redoing one ticker is a legitimate request.
        researched(repo, "ALF.NZ", days_old=0)
        assert filter_tickers.eligible(repo, ["ALF.NZ"], force=True) == \
            ["ALF.NZ"]


class TestSkipResearch:
    """`skip_research` in info.json excludes a ticker from bulk runs.

    CRP.NZ was suspended from NZX quotation on 3-Aug-2026 after a TSXV
    delisting: the NZ$0.038 quote is frozen, the company is a pre-revenue
    explorer with a going-concern warning, and it reports in CAD while quoted
    in NZD. A DCF on it is meaningless, but it still cost $4.10 to rediscover
    that. Commenting it out of the queue stops the selector reaching it;
    this stops a pasted ticker list reaching it too.
    """

    def _mark(self, repo, ticker, **extra):
        d = repo / "research" / ticker
        d.mkdir(parents=True, exist_ok=True)
        (d / "info.json").write_text(json.dumps({"skip_research": True,
                                                 **extra}))

    def test_a_skipped_ticker_is_dropped(self, repo):
        self._mark(repo, "CRP.NZ", skip_reason="suspended")
        assert filter_tickers.eligible(repo, ["CRP.NZ", "OK.NZ"]) == ["OK.NZ"]

    def test_force_overrides_the_skip(self, repo):
        # A deliberate re-run must still be possible.
        self._mark(repo, "CRP.NZ")
        assert filter_tickers.eligible(repo, ["CRP.NZ"], force=True) == \
            ["CRP.NZ"]

    def test_skip_research_false_is_not_a_skip(self, repo):
        d = repo / "research" / "OK.NZ"
        d.mkdir(parents=True)
        (d / "info.json").write_text(json.dumps({"skip_research": False}))
        assert filter_tickers.eligible(repo, ["OK.NZ"]) == ["OK.NZ"]

    def test_an_unparseable_info_json_does_not_skip(self, repo):
        # Malformed metadata must not silently drop a ticker from every run.
        d = repo / "research" / "ODD.NZ"
        d.mkdir(parents=True)
        (d / "info.json").write_text("{not json")
        assert filter_tickers.eligible(repo, ["ODD.NZ"]) == ["ODD.NZ"]

    def test_no_info_json_does_not_skip(self, repo):
        assert filter_tickers.eligible(repo, ["NEW.NZ"]) == ["NEW.NZ"]


class TestOrdering:
    def test_unresearched_tickers_come_before_stale_ones(self, repo):
        # "new first, then refresh the stalest" -- the policy select_ticker
        # already applies to the queue, now applied to an explicit list too.
        researched(repo, "STALE.NZ", days_old=200)
        got = filter_tickers.eligible(repo, ["STALE.NZ", "NEW.NZ"],
                                      stale_days=45)
        assert got == ["NEW.NZ", "STALE.NZ"]

    def test_stale_tickers_are_ordered_oldest_first(self, repo):
        researched(repo, "A.NZ", days_old=60)
        researched(repo, "B.NZ", days_old=300)
        got = filter_tickers.eligible(repo, ["A.NZ", "B.NZ"], stale_days=45)
        assert got == ["B.NZ", "A.NZ"]

    def test_supplied_order_is_kept_among_unresearched(self, repo):
        got = filter_tickers.eligible(repo, ["Z.NZ", "A.NZ", "M.NZ"])
        assert got == ["Z.NZ", "A.NZ", "M.NZ"]


class TestLimit:
    def test_limit_truncates(self, repo):
        got = filter_tickers.eligible(repo, ["A.NZ", "B.NZ", "C.NZ"], limit=2)
        assert got == ["A.NZ", "B.NZ"]

    def test_limit_zero_means_no_limit(self, repo):
        got = filter_tickers.eligible(repo, ["A.NZ", "B.NZ"], limit=0)
        assert len(got) == 2

    def test_the_limit_applies_after_filtering(self, repo):
        # -n 20 should yield 20 tickers worth doing, not 20 candidates of
        # which six are already finished.
        researched(repo, "DONE.NZ", days_old=0)
        got = filter_tickers.eligible(repo, ["DONE.NZ", "A.NZ", "B.NZ"],
                                      limit=2)
        assert got == ["A.NZ", "B.NZ"]


class TestCli:
    def _run(self, monkeypatch, capsys, repo, *argv):
        monkeypatch.setattr("sys.argv", ["filter_tickers.py", *argv])
        monkeypatch.setattr(filter_tickers, "REPO", repo)
        filter_tickers.main()
        return capsys.readouterr().out.split()

    def test_prints_one_ticker_per_line(self, repo, monkeypatch, capsys):
        researched(repo, "DONE.NZ", days_old=0)
        got = self._run(monkeypatch, capsys, repo, "DONE.NZ", "A.NZ", "B.NZ")
        assert got == ["A.NZ", "B.NZ"]

    def test_limit_flag(self, repo, monkeypatch, capsys):
        got = self._run(monkeypatch, capsys, repo, "--limit", "1",
                        "A.NZ", "B.NZ")
        assert got == ["A.NZ"]

    def test_force_flag(self, repo, monkeypatch, capsys):
        researched(repo, "DONE.NZ", days_old=0)
        got = self._run(monkeypatch, capsys, repo, "--force", "DONE.NZ")
        assert got == ["DONE.NZ"]

    def test_nothing_eligible_prints_nothing(self, repo, monkeypatch, capsys):
        researched(repo, "DONE.NZ", days_old=0)
        assert self._run(monkeypatch, capsys, repo, "DONE.NZ") == []
