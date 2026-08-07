"""screen_metrics.py compares core metrics across ticker DBs on one scale.

It reads metrics_normalized (never core_metrics) so every value arrives in
millions of the reporting currency, opens each DB separately because
ATTACHing several catalogs makes the view's unqualified core_metrics
reference ambiguous, and skips DBs that predate the view rather than
failing the whole screen.
"""

import sys

import duckdb
import pytest
import schema
import screen_metrics


def make_db(repo, ticker, rows):
    """Create research/{T}/Reports/{T}.duckdb with the canonical schema."""
    d = repo / "research" / ticker / "Reports"
    d.mkdir(parents=True)
    con = duckdb.connect(str(d / f"{ticker}.duckdb"))
    con.execute(schema.create_sql())
    for period, revenue, net_income, eps, units, currency in rows:
        con.execute(
            "INSERT INTO core_metrics"
            " (period, revenue, net_income, eps, units, currency)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [period, revenue, net_income, eps, units, currency])
    con.close()


@pytest.fixture
def repo(monkeypatch, tmp_path):
    """Retarget the module-level REPO glob at a tmp research/ tree."""
    monkeypatch.setattr(screen_metrics, "REPO", tmp_path)
    return tmp_path


class TestLoad:
    def test_values_arrive_in_millions(self, repo):
        # NZX filings are usually thousands; the view is what rescales.
        make_db(repo, "AGL.NZ",
                [("FY2024", 400_000.0, 20_000.0, 0.5, "thousands", "NZD")])
        rows = screen_metrics.load(set(), "FY2024")
        assert len(rows) == 1
        row = rows[0]
        assert row["ticker"] == "AGL.NZ"
        assert row["revenue"] == 400.0
        assert row["net_income"] == 20.0
        assert row["eps"] == 0.5  # per-share figures are never rescaled
        assert row["currency"] == "NZD"

    def test_unknown_units_yield_null_not_a_guess(self, repo):
        # The SEK.NZ rule: a missing value is obvious, a plausible wrong
        # one is not. Unrecorded units must surface as None here too.
        make_db(repo, "SEK.NZ",
                [("FY2024", 400_000.0, None, None, "NZ$000", "NZD")])
        rows = screen_metrics.load(set(), "FY2024")
        assert rows[0]["revenue"] is None

    def test_period_filter(self, repo):
        make_db(repo, "NFLX", [
            ("FY2023", 31.6, 4.5, 1.0, "billions", "USD"),
            ("FY2024", 39.0, 8.7, 2.0, "billions", "USD"),
        ])
        rows = screen_metrics.load(set(), "FY2024")
        assert [r["period"] for r in rows] == ["FY2024"]
        assert len(screen_metrics.load(set(), "")) == 2  # empty = all periods

    def test_ticker_filter(self, repo):
        make_db(repo, "NFLX", [("FY2024", 39.0, 8.7, 2.0, "billions", "USD")])
        make_db(repo, "AGL.NZ", [("FY2024", 400.0, 20.0, 0.5, "millions", "NZD")])
        rows = screen_metrics.load({"NFLX"}, "FY2024")
        assert [r["ticker"] for r in rows] == ["NFLX"]

    def test_db_without_the_view_is_skipped(self, repo):
        # A legacy DB that was never re-extracted has no metrics_normalized;
        # it must be skipped rather than failing the whole screen.
        make_db(repo, "NFLX", [("FY2024", 39.0, 8.7, 2.0, "billions", "USD")])
        d = repo / "research" / "OLD.NZ" / "Reports"
        d.mkdir(parents=True)
        con = duckdb.connect(str(d / "OLD.NZ.duckdb"))
        con.execute("CREATE TABLE core_metrics (period TEXT)")
        con.close()
        rows = screen_metrics.load(set(), "FY2024")
        assert [r["ticker"] for r in rows] == ["NFLX"]


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["screen_metrics.py", *argv])
    return screen_metrics.main()


class TestMain:
    def test_ranks_by_absolute_magnitude_descending(self, repo, monkeypatch, capsys):
        make_db(repo, "BIGCO", [("FY2024", 500.0, 50.0, 1.0, "millions", "USD")])
        make_db(repo, "LOSSCO", [("FY2024", -800.0, -80.0, -1.0, "millions", "USD")])
        make_db(repo, "TINY", [("FY2024", 10.0, 1.0, 0.1, "millions", "USD")])
        assert run(monkeypatch, "--period", "FY2024") == 0
        out = capsys.readouterr().out
        # abs() ordering: a big loss outranks a smaller positive revenue
        assert out.index("LOSSCO") < out.index("BIGCO") < out.index("TINY")
        assert "3 ticker(s) with data for FY2024" in out

    def test_top_limits_printed_rows_not_the_count(self, repo, monkeypatch, capsys):
        make_db(repo, "BIGCO", [("FY2024", 500.0, 50.0, 1.0, "millions", "USD")])
        make_db(repo, "TINY", [("FY2024", 10.0, 1.0, 0.1, "millions", "USD")])
        assert run(monkeypatch, "--period", "FY2024", "--top", "1") == 0
        out = capsys.readouterr().out
        assert "BIGCO" in out
        assert "TINY" not in out
        assert "2 ticker(s)" in out  # footer still counts everything loaded

    def test_rows_missing_the_sort_key_are_dropped(self, repo, monkeypatch, capsys):
        make_db(repo, "BIGCO", [("FY2024", 500.0, 50.0, 1.0, "millions", "USD")])
        make_db(repo, "NOREV", [("FY2024", None, 9.0, 0.5, "millions", "USD")])
        assert run(monkeypatch, "--period", "FY2024") == 0
        out = capsys.readouterr().out
        assert "NOREV" not in out
        assert "1 ticker(s)" in out

    def test_order_flag_overrides_the_sort_key(self, repo, monkeypatch, capsys):
        make_db(repo, "BIGCO", [("FY2024", 500.0, 5.0, 1.0, "millions", "USD")])
        make_db(repo, "TINY", [("FY2024", 10.0, 90.0, 0.1, "millions", "USD")])
        assert run(monkeypatch, "--period", "FY2024", "--order", "net_income") == 0
        out = capsys.readouterr().out
        assert out.index("TINY") < out.index("BIGCO")

    def test_order_typo_is_rejected_by_argparse(self, repo, monkeypatch, capsys):
        # --order takes the same keys as --metric, so a typo must die at the
        # parser with the legal choices listed -- not silently filter every
        # row and report a misleading "no data for period" error.
        make_db(repo, "BIGCO", [("FY2024", 500.0, 50.0, 1.0, "millions", "USD")])
        with pytest.raises(SystemExit) as exc:
            run(monkeypatch, "--period", "FY2024", "--order", "revnue")
        assert exc.value.code == 2  # argparse usage error
        assert "invalid choice" in capsys.readouterr().err

    def test_no_data_exits_one_with_hint(self, repo, monkeypatch, capsys):
        make_db(repo, "BIGCO", [("FY2024", 500.0, 50.0, 1.0, "millions", "USD")])
        assert run(monkeypatch, "--period", "FY1999") == 1
        err = capsys.readouterr().err
        assert "no data for period FY1999" in err
        assert "normalized view" in err

    def test_null_companion_columns_render_as_dash(self, repo, monkeypatch, capsys):
        make_db(repo, "BIGCO", [("FY2024", 500.0, None, None, "millions", "USD")])
        assert run(monkeypatch, "--period", "FY2024") == 0
        out = capsys.readouterr().out
        line = next(ln for ln in out.splitlines() if "BIGCO" in ln)
        assert line.split()[-2:] == ["-", "-"]  # net_income and eps columns
