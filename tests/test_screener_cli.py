"""End-to-end CLI coverage for the screener and the units backfill.

These drive the real entry points against a temporary repo built from the
canonical schema, so the DuckDB reads, argument parsing and report rendering
are exercised rather than mocked.
"""

import json

import backfill_units
import duckdb
import fundamentals
import pytest
import schema
import screen_fundamentals as sf


class _Repo:
    """A temp repo root that can be given tickers backed by real DuckDBs.

    Behaves as the path itself for the `/` operator and str(), so tests can
    pass it straight to --root.
    """

    def __init__(self, root):
        self.root = root
        (root / "research").mkdir()

    def __truediv__(self, other):
        return self.root / other

    def __fspath__(self):
        return str(self.root)

    def __str__(self):
        return str(self.root)

    def add(self, ticker, rows, units="millions", currency="NZD", dcf=None):
        reports = self.root / "research" / ticker / "Reports"
        reports.mkdir(parents=True)
        con = duckdb.connect(str(reports / f"{ticker}.duckdb"))
        con.execute(schema.create_sql())
        for period, vals in rows:
            cols = ["period", "units", "currency", *vals]
            args = [period, units, currency, *vals.values()]
            con.execute(
                f"INSERT INTO core_metrics ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})", args)
        con.close()
        if dcf is not None:
            (reports / f"{ticker}_DCF.json").write_text(json.dumps(dcf))
        return reports


@pytest.fixture
def repo(tmp_path):
    return _Repo(tmp_path)


HLG = [
    ("FY2023", {"revenue": 409.7, "net_income": 32.0, "free_cash_flow": 53.2,
                "shareholders_equity": 96.3, "total_debt": 0.0,
                "shares_outstanding": 59.0}),
    ("FY2024", {"revenue": 435.6, "net_income": 34.5, "free_cash_flow": 69.4,
                "shareholders_equity": 103.2, "total_debt": 0.0,
                "shares_outstanding": 59.0}),
    ("FY2025", {"revenue": 470.7, "net_income": 39.5, "free_cash_flow": 72.8,
                "shareholders_equity": 111.9, "total_debt": 0.0,
                "shares_outstanding": 59.0}),
    # Half-year FCF is present so all three TTM fields reconstruct as FY+H1;
    # omit it and the row is correctly demoted to FY-BASIS.
    ("H1 FY2024", {"revenue": 223.0, "net_income": 21.1,
                   "free_cash_flow": 35.7}),
    ("H1 FY2025", {"revenue": 240.0, "net_income": 21.2,
                   "free_cash_flow": 33.6}),
    ("H1 FY2026", {"revenue": 275.2, "net_income": 28.0,
                   "shareholders_equity": 121.5, "total_debt": 0.0,
                   "shares_outstanding": 59.0, "free_cash_flow": 40.9}),
]

HLG_DCF = {"current_price": 10.65, "currency": "NZD",
           "historical_growth": {"selected_growth_rate": 8.0}}


class TestScreenerCli:
    def test_passes_a_real_ticker_end_to_end(self, repo, capsys):
        repo.add("HLG.NZ", HLG, dcf=HLG_DCF)
        code = sf.main(["--root", str(repo), "--exchange", "NZX",
                        "--min-roe", "0.15", "--min-fcf", "0"])
        out = capsys.readouterr().out
        assert code == 0
        assert "HLG.NZ" in out
        assert "PASS (1)" in out

    def test_a_field_missing_its_half_year_demotes_the_row_to_fy_basis(self, repo, capsys):
        """Revenue and earnings reconstruct, FCF does not -- so the whole row
        is FY-BASIS and held back from PASS unless explicitly allowed."""
        no_h1_fcf = [(p, {k: v for k, v in vals.items()
                          if not (p.startswith("H1") and k == "free_cash_flow")})
                     for p, vals in HLG]
        repo.add("HLG.NZ", no_h1_fcf, dcf=HLG_DCF)
        assert sf.main(["--root", str(repo), "--min-roe", "0.15"]) == 1
        assert "FY-BASIS (1)" in capsys.readouterr().out
        assert sf.main(["--root", str(repo), "--min-roe", "0.15",
                        "--allow-fy-basis"]) == 0

    def test_exit_code_1_when_nothing_passes(self, repo, capsys):
        repo.add("HLG.NZ", HLG, dcf=HLG_DCF)
        assert sf.main(["--root", str(repo), "--min-roe", "0.99"]) == 1
        assert "PASS (0)" in capsys.readouterr().out

    def test_bad_exchange_exits_2(self, repo, capsys):
        assert sf.main(["--root", str(repo), "--exchange", "NASDAQ"]) == 2
        assert "unknown exchange" in capsys.readouterr().err

    def test_conflicting_filters_exit_2(self, repo, capsys):
        assert sf.main(["--root", str(repo), "--exchange", "NZX",
                        "--suffix", ".L"]) == 2
        assert "disagree" in capsys.readouterr().err

    def test_cagr_ambiguity_note_goes_to_stderr(self, repo, capsys):
        repo.add("HLG.NZ", HLG, dcf=HLG_DCF)
        sf.main(["--root", str(repo), "--min-revenue-cagr-5y", "0.5"])
        assert "7.6x over five years" in capsys.readouterr().err

    def test_json_output_round_trips(self, repo, tmp_path, capsys):
        repo.add("HLG.NZ", HLG, dcf=HLG_DCF)
        out = tmp_path / "screen.json"
        sf.main(["--root", str(repo), "--min-roe", "0.15",
                 "--json", str(out)])
        capsys.readouterr()
        data = json.loads(out.read_text())
        assert data["passed"][0]["ticker"] == "HLG.NZ"
        assert data["passed"][0]["ttm_revenue"] == pytest.approx(505.9)

    def test_ticker_filter_narrows_the_scan(self, repo, capsys):
        repo.add("HLG.NZ", HLG, dcf=HLG_DCF)
        repo.add("SEK.NZ", HLG, dcf=HLG_DCF)
        sf.main(["--root", str(repo), "--ticker", "HLG.NZ"])
        out = capsys.readouterr().out
        assert "HLG.NZ" in out
        assert "SEK.NZ" not in out

    def test_an_unreadable_db_is_reported_not_fatal(self, repo, capsys):
        reports = repo / "research" / "BROKEN.NZ" / "Reports"
        reports.mkdir(parents=True)
        (reports / "BROKEN.NZ.duckdb").write_text("not a database")
        assert sf.main(["--root", str(repo), "--min-roe", "0.15"]) == 1
        assert "BROKEN.NZ" in capsys.readouterr().out


class TestScanIo:
    def test_scan_reads_a_real_database(self, repo):
        repo.add("HLG.NZ", HLG, dcf=HLG_DCF)
        rows = fundamentals.scan(repo.root, suffix=".NZ")
        assert [r.ticker for r in rows] == ["HLG.NZ"]
        assert rows[0].ttm_revenue == pytest.approx(505.9)
        assert rows[0].peg is not None

    def test_a_malformed_dcf_is_ignored_not_fatal(self, repo):
        reports = repo.add("HLG.NZ", HLG)
        (reports / "HLG.NZ_DCF.json").write_text("{not json")
        assert fundamentals.load_dcf(repo.root, "HLG.NZ") is None
        assert fundamentals.scan(repo.root)[0].ttm_revenue == pytest.approx(505.9)

    def test_missing_dcf_returns_none(self, repo):
        repo.add("HLG.NZ", HLG)
        assert fundamentals.load_dcf(repo.root, "HLG.NZ") is None


class TestBackfillCli:
    def test_dry_run_reports_without_writing(self, repo, capsys):
        rows = [("FY2025", {"total_debt": 100.0, "free_cash_flow": 50.0})]
        reports = repo.add("T.NZ", rows, units=None)
        (reports / "T.NZ_DCF.json").write_text(
            json.dumps({"inputs": {"total_debt": 100.0, "last_fcf": 50.0}}))
        assert backfill_units.main(["--root", str(repo)]) == 0
        out = capsys.readouterr().out
        assert "RESOLVED (1)" in out
        assert "Dry run" in out
        con = duckdb.connect(str(reports / "T.NZ.duckdb"), read_only=True)
        assert con.execute("SELECT units FROM core_metrics").fetchone()[0] is None
        con.close()

    def test_apply_writes_and_is_idempotent(self, repo, capsys):
        rows = [("FY2025", {"total_debt": 100.0, "free_cash_flow": 50.0})]
        reports = repo.add("T.NZ", rows, units=None)
        (reports / "T.NZ_DCF.json").write_text(
            json.dumps({"inputs": {"total_debt": 100.0, "last_fcf": 50.0}}))
        backfill_units.main(["--root", str(repo), "--apply"])
        capsys.readouterr()
        con = duckdb.connect(str(reports / "T.NZ.duckdb"), read_only=True)
        assert con.execute("SELECT units FROM core_metrics").fetchone()[0] == "millions"
        con.close()
        backfill_units.main(["--root", str(repo), "--apply"])
        assert "RESOLVED (0)" in capsys.readouterr().out

    def test_refusal_is_reported(self, repo, capsys):
        rows = [("FY2025", {"total_debt": 100.0})]
        reports = repo.add("T.NZ", rows, units=None)
        (reports / "T.NZ_DCF.json").write_text(
            json.dumps({"inputs": {"total_debt": 100.0}}))
        backfill_units.main(["--root", str(repo)])
        out = capsys.readouterr().out
        assert "REFUSED (1)" in out
        assert "insufficient-anchors" in out

    def test_tickers_already_carrying_units_are_skipped(self, repo, capsys):
        repo.add("T.NZ", [("FY2025", {"revenue": 1.0})], units="millions")
        backfill_units.main(["--root", str(repo)])
        out = capsys.readouterr().out
        assert "RESOLVED (0)" in out
        assert "REFUSED (0)" in out

    def test_a_malformed_dcf_yields_a_refusal(self, repo, capsys):
        reports = repo.add("T.NZ", [("FY2025", {"revenue": 1.0})], units=None)
        (reports / "T.NZ_DCF.json").write_text("{not json")
        backfill_units.main(["--root", str(repo)])
        assert "no-dcf-inputs" in capsys.readouterr().out
