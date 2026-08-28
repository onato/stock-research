"""Which skill a ticker should be researched with, given its refresh tier.

`refresh_plan.py` has classified tickers into tiers 0-3 since it was written,
but only `has_new_filings` was ever consumed (by select_ticker and
filter_tickers). `plan_tier` itself had no caller outside its own CLI, so
tier 2 -- stale by date, no new filings -- had nowhere to go.

That left two options for a stale ticker: a free price write-back, or a ~$6
full re-research. Everything in tier 2 falls between them, and `make run`
with --require-new-filings skips those tickers entirely, so they just keep
aging. 23 of 121 researched tickers sit there; DCBO had been stale 48 days.

This module maps a tier to the skill that should run, so lib.sh can stop
hardcoding `/research-stock`.
"""

import datetime as dt
import json

import pytest
import refresh_route


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "research").mkdir()
    return tmp_path


def ticker_at(repo, ticker, *, filings=(), csv_periods=(), days_old=0):
    base = repo / "research" / ticker
    (base / "Extracted").mkdir(parents=True, exist_ok=True)
    (base / "Reports").mkdir(parents=True, exist_ok=True)
    for name in filings:
        (base / "Extracted" / name).write_text("x")
    if csv_periods:
        rows = "\n".join(f"{p},1" for p in csv_periods)
        (base / "Reports" / f"{ticker}_Metrics.csv").write_text(
            "Period,Revenue\n" + rows)
    when = dt.date.today() - dt.timedelta(days=days_old)
    (base / "Reports" / f"{ticker}_DCF.json").write_text(json.dumps({
        "valuation_date": when.isoformat(),
        "current_price": 100.0,
    }))
    (base / "Reports" / f"{ticker}_Dashboard.html").write_text("x")


class TestRoute:
    def test_tier_3_gets_the_full_research_skill(self, repo):
        """A filing the CSV has not absorbed: the parser must run."""
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2025"], days_old=60)
        r = refresh_route.route(repo, "X")
        assert r.tier == 3
        assert r.skill == "research-stock"

    def test_tier_2_gets_the_cheap_refresh_skill(self, repo):
        """DCBO's case: stale 48d, but the CSV already has the newest filing."""
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2026"], days_old=60)
        r = refresh_route.route(repo, "X")
        assert r.tier == 2
        assert r.skill == "refresh-stock"

    def test_tier_1_runs_no_model_at_all(self, repo):
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2026"], days_old=1)
        r = refresh_route.route(repo, "X")
        assert r.tier == 1
        assert r.skill is None

    def test_tier_0_runs_no_model_either(self, repo):
        """A price move is arithmetic; refresh_price.py handles it for free."""
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2026"], days_old=1)
        r = refresh_route.route(repo, "X", live_price=200.0)
        assert r.tier == 0
        assert r.skill is None

    def test_an_unresearched_ticker_is_tier_3(self, repo):
        (repo / "research" / "NEW").mkdir()
        r = refresh_route.route(repo, "NEW")
        assert r.tier == 3
        assert r.skill == "research-stock"

    def test_the_reason_survives_for_the_operator(self, repo):
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2026"], days_old=60)
        # The point is that plan_tier's reason reaches the operator intact,
        # not the exact wording -- assert on the tier-2 substance instead.
        assert "stale (60d)" in refresh_route.route(repo, "X").reason


class TestPrompt:
    """The exact string lib.sh sends to the CLI."""

    def test_tier_2_prompt_names_the_refresh_skill(self, repo):
        ticker_at(repo, "DCBO", filings=["DCBO_PR_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=60)
        assert refresh_route.prompt(repo, "DCBO") == "/refresh-stock DCBO"

    def test_tier_3_prompt_names_the_research_skill(self, repo):
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2025"], days_old=60)
        assert refresh_route.prompt(repo, "X") == "/research-stock X"

    def test_no_work_yields_an_empty_prompt(self, repo):
        """An empty prompt is the signal to skip -- never a bare skill name."""
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2026"], days_old=1)
        assert refresh_route.prompt(repo, "X") == ""

    def test_force_overrides_a_cheap_tier(self, repo):
        """`make run TICKER=X FORCE=1` must still do the expensive thing."""
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2026"], days_old=1)
        assert refresh_route.prompt(repo, "X", force=True) == "/research-stock X"


class TestCli:
    """lib.sh reads stdout directly, so the CLI contract is load-bearing."""

    def test_prompt_mode_prints_only_the_prompt(self, repo, monkeypatch,
                                                capsys):
        ticker_at(repo, "DCBO", filings=["DCBO_PR_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=60)
        monkeypatch.setattr(refresh_route, "REPO", repo)
        monkeypatch.setattr("sys.argv",
                            ["refresh_route.py", "--ticker", "DCBO",
                             "--prompt"])
        assert refresh_route.main() == 0
        assert capsys.readouterr().out == "/refresh-stock DCBO\n"

    def test_prompt_mode_prints_an_empty_line_when_there_is_no_work(
            self, repo, monkeypatch, capsys):
        """lib.sh treats empty stdout as "skip" -- exit status stays 0."""
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2026"], days_old=1)
        monkeypatch.setattr(refresh_route, "REPO", repo)
        monkeypatch.setattr("sys.argv",
                            ["refresh_route.py", "--ticker", "X", "--prompt"])
        assert refresh_route.main() == 0
        assert capsys.readouterr().out.strip() == ""

    def test_human_mode_names_the_tier_and_skill(self, repo, monkeypatch,
                                                 capsys):
        ticker_at(repo, "DCBO", filings=["DCBO_PR_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=60)
        monkeypatch.setattr(refresh_route, "REPO", repo)
        monkeypatch.setattr("sys.argv", ["refresh_route.py", "DCBO"])
        assert refresh_route.main() == 0
        out = capsys.readouterr().out
        assert "tier 2" in out
        assert "/refresh-stock" in out

    def test_human_mode_marks_a_no_model_tier(self, repo, monkeypatch,
                                              capsys):
        ticker_at(repo, "X", filings=["X_Annual_FY2026.txt"],
                  csv_periods=["FY2026"], days_old=1)
        monkeypatch.setattr(refresh_route, "REPO", repo)
        monkeypatch.setattr("sys.argv", ["refresh_route.py", "X"])
        refresh_route.main()
        assert "(no model)" in capsys.readouterr().out

    def test_a_missing_ticker_is_an_error(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["refresh_route.py"])
        with pytest.raises(SystemExit):
            refresh_route.main()
