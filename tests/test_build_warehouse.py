"""scripts/build_warehouse.py -- state/research.duckdb from the committed files.

The per-ticker DuckDBs are caches; the committed Reports/ files are the
record. This builds the one cross-ticker database from those files alone --
Metrics CSVs, DCF JSONs (and Drivers), Analysis JSONs, Prices CSVs,
Corrections logs, info.json, the ledger -- so a change of schema is a
rebuild, never a migration, and every row can be traced to a committed file.
"""

import json
import shutil
from pathlib import Path

import build_warehouse as bw
import dcf_engine
import duckdb
import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    # APA.AX: engine-built DCF with a Drivers file, metrics in millions.
    apa = root / "research" / "APA.AX"
    (apa / "Reports").mkdir(parents=True)
    (apa / "info.json").write_text(json.dumps({"name": "APA Group", "sector": "Energy Infrastructure",
                                               "fiscal_year_end": "06-30", "updated_at": "2026-09-21"}))
    drivers = json.loads((FIX / "dcf" / "APA_Drivers.json").read_text())
    (apa / "Reports" / "APA.AX_Drivers.json").write_text(json.dumps(drivers))
    (apa / "Reports" / "APA.AX_DCF.json").write_text(json.dumps(dcf_engine.build(drivers)))
    (apa / "Reports" / "APA.AX_Metrics.csv").write_text(
        "Period,Revenue,EBITDA,NetIncome,FreeCashFlow,SharesOutstanding,EPS,DistributionPerSecurity,Units,Currency\n"
        "FY2025,3179,1894,129,1083,1295,0.099,57.0,millions,AUD\n"
        "H1 FY2026,1614,1046,95,556,1312,0.073,29.0,millions,AUD\n"
        "FY2026,2977,2095,234,1118,1316,0.178,58.0,millions,AUD\n")
    (apa / "Reports" / "APA.AX_Prices.csv").write_text("Date,Close\n2026-08-31,10.5\n2026-09-21,10.87\n")
    (apa / "Reports" / "APA.AX_Analysis.json").write_text(json.dumps(
        {"company_name": "APA Group", "risks": [{"name": "WGP recontracting"}]}))
    (apa / "Reports" / "APA.AX_Corrections.jsonl").write_text(json.dumps(
        {"ts": "2026-09-22T01:00:00Z", "target": "core_metrics", "period": "FY2025", "col": "revenue",
         "old_value": 3178.0, "new_value": 3179.0, "source": "AR p.5", "actor": "test", "op": "set"}) + "\n")
    # TEST: a legacy hand-written DCF (no Drivers), metrics in thousands, A$ currency label.
    t = root / "research" / "TEST"
    (t / "Reports").mkdir(parents=True)
    (t / "info.json").write_text(json.dumps({"name": "Test Co", "sector": "Software", "fiscal_year_end": "06-30"}))
    shutil.copy(FIX / "dashboard" / "TEST_DCF.json", t / "Reports" / "TEST_DCF.json")
    (t / "Reports" / "TEST_Metrics.csv").write_text(
        "Period,Revenue,NetIncome,Units,Currency\nFY2024,100000,5000,thousands,NZD\nFY2025,120000,6000,thousands,NZD\n")
    # A stub with info.json only (never researched).
    (root / "research" / "STUB.NZ").mkdir()
    (root / "research" / "STUB.NZ" / "info.json").write_text(json.dumps({"name": "Stub", "sector": "Unknown"}))
    (root / "evals").mkdir()
    (root / "evals" / "ledger.jsonl").write_text(json.dumps(
        {"ticker": "TEST", "logged_at": "2026-08-26T00:00:00Z", "valuation_date": "2026-08-26",
         "current_price": 4.0, "weighted_iv": {"weighted_iv": 3.51}, "weighted_upside": {"weighted_upside": -12.3},
         "valuation_model": "owner-fcf", "git_head": "abc1234"}) + "\n")
    (root / "state").mkdir()
    return root


@pytest.fixture(scope="module")
def db(repo):
    out = repo / "state" / "research.duckdb"
    bw.build(repo, out)
    con = duckdb.connect(str(out), read_only=True)
    yield con
    con.close()


def one(con, sql):
    return con.execute(sql).fetchone()


class TestCompanies:
    def test_every_info_json_is_a_company_with_coverage_flags(self, db):
        rows = {r[0]: r for r in db.execute(
            "select ticker, name, sector, exchange, has_metrics, has_dcf, has_analysis from companies").fetchall()}
        assert rows["APA.AX"][1:] == ("APA Group", "Energy Infrastructure", "AX", True, True, True)
        assert rows["STUB.NZ"][4:] == (False, False, False)
        assert rows["TEST"][3] is None                       # bare US-style symbol


class TestMetrics:
    def test_rows_carry_parsed_period_columns(self, db):
        r = one(db, "select period_type, fiscal_year, months, revenue, units, currency from metrics "
                    "where ticker='APA.AX' and period='H1 FY2026'")
        assert r == ("H1", 2026, 6, 1614.0, "millions", "AUD")

    def test_headers_normalise_to_core_columns(self, db):
        assert one(db, "select eps, shares_outstanding from metrics where ticker='APA.AX' and period='FY2026'") == (0.178, 1316.0)

    def test_metrics_m_scales_thousands_to_millions(self, db):
        assert one(db, "select revenue from metrics_m where ticker='TEST' and period='FY2025'") == (120.0,)
        assert one(db, "select revenue from metrics_m where ticker='APA.AX' and period='FY2026'") == (2977.0,)

    def test_non_core_columns_become_kpis(self, db):
        assert one(db, "select value from kpis where ticker='APA.AX' and period='FY2026' and name='DistributionPerSecurity'") == (58.0,)
        assert one(db, "select count(*) from kpis where ticker='TEST'") == (0,)

    def test_latest_annual_view(self, db):
        rows = dict(db.execute("select ticker, period from latest_annual").fetchall())
        assert rows == {"APA.AX": "FY2026", "TEST": "FY2025"}


class TestDcf:
    def test_one_summary_row_per_dcf(self, db):
        r = one(db, "select valuation_date, current_price, weighted_iv, weighted_upside_pct, currency, quote_currency, "
                    "model_family, has_drivers, engine_version, hurdle_rate from dcf where ticker='APA.AX'")
        assert r == ("2026-09-22", 10.83, 7.33, -32.3, "AUD", "AUD", "owner_fcf", True, 1, 0.15)
        t = one(db, "select weighted_iv, has_drivers, engine_version, currency from dcf where ticker='TEST'")
        assert t == (3.51, False, None, "A$")

    def test_scenarios_with_canonical_columns(self, db):
        r = one(db, "select intrinsic_value, street_intrinsic_value, entry_price, weight, wacc, terminal_growth, "
                    "terminal_cap_multiple, terminal_value_bound, engine_check from dcf_scenarios "
                    "where ticker='APA.AX' and scenario='base'")
        assert r == (8.0, 8.07, -2.64, 0.5, 7.0, 2.5, 18.0, "cap", "ok")
        assert one(db, "select intrinsic_value, engine_check from dcf_scenarios where ticker='TEST' and scenario='base'") == (3.39, "no-engine")

    def test_every_numeric_key_survives_in_dcf_values(self, db):
        # The 15 spellings of intrinsic value in the corpus are data, not noise.
        assert one(db, "select value from dcf_values where ticker='APA.AX' and block='valuation' and scenario='base' "
                       "and key='implied_terminal_ev_to_ebitda'") == (9.6,)
        assert one(db, "select value from dcf_values where ticker='APA.AX' and block='entry_price' and scenario='all' "
                       "and key='weighted_entry_price'") == (-2.83,)

    def test_driver_paths_are_one_row_per_year(self, db):
        rows = db.execute("select year_index, growth_pct, ebitda_margin_pct, capex_pct, fcf from dcf_drivers "
                          "where ticker='APA.AX' and scenario='base' order by year_index").fetchall()
        assert len(rows) == 10
        assert rows[0] == (1, 4.0, 72.4, 31.0, 886.7)
        assert rows[-1][1] == 2.5
        # TEST's legacy DCF has growth_rates but no margin path: growth only.
        assert one(db, "select count(*), max(ebitda_margin_pct) from dcf_drivers where ticker='TEST'")[1] is None

    def test_the_document_is_queryable_as_json(self, db):
        assert one(db, "select doc->>'$.inputs.fy0' from dcf where ticker='APA.AX'") == ("FY2026",)
        assert one(db, "select json_extract_string(doc, '$.risks[0].name') from analysis where ticker='APA.AX'") == ("WGP recontracting",)


class TestOtherSources:
    def test_prices(self, db):
        assert one(db, "select count(*), max(close) from prices where ticker='APA.AX'") == (2, 10.87)

    def test_ledger(self, db):
        assert one(db, "select weighted_iv, weighted_upside, valuation_model from ledger where ticker='TEST'") == (3.51, -12.3, "owner-fcf")

    def test_corrections(self, db):
        assert one(db, "select col, old_value, new_value from corrections where ticker='APA.AX'") == ("revenue", 3178.0, 3179.0)

    def test_build_info_records_provenance(self, db):
        r = one(db, "select tickers, metrics_rows, dcf_rows from build_info")
        assert r == (3, 5, 2)


class TestScreenView:
    def test_screen_joins_the_pieces(self, db):
        r = one(db, "select name, latest_period, revenue_m, weighted_iv, upside_pct from screen where ticker='APA.AX'")
        assert r == ("APA Group", "FY2026", 2977.0, 7.33, -32.3)


class TestRebuild:
    def test_rebuild_is_idempotent_and_atomic(self, repo, tmp_path):
        out = tmp_path / "wh.duckdb"
        bw.build(repo, out)
        first = duckdb.connect(str(out), read_only=True).execute("select count(*) from metrics").fetchone()
        bw.build(repo, out)
        second = duckdb.connect(str(out), read_only=True).execute("select count(*) from metrics").fetchone()
        assert first == second == (5,)
        assert not (tmp_path / "wh.duckdb.tmp").exists()

    def test_main_prints_counts(self, repo, tmp_path, capsys):
        assert bw.main(["--research", str(repo / "research"), "--out", str(tmp_path / "x.duckdb"),
                        "--ledger", str(repo / "evals" / "ledger.jsonl")]) == 0
        out = capsys.readouterr().out
        assert "companies 3" in out
        assert "dcf 2" in out
