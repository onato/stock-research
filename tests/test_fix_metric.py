"""Tests for fix_metric.py: hand corrections that land in the DB, not the CSV.

DCBO's FY2022 row was corrected by hand in the CSV (commit 197bbd9a) and
clobbered nine days later when the next export regenerated the CSV from a
DB that still held the old values. The DB-first rule needs a tool that
makes the DB the place a fix goes, records what it replaced and why, and
mirrors that record into a committed file the gitignored DB can be rebuilt
from. Every DB here is in-memory or under tmp_path.
"""

import json
import sys

import duckdb
import fix_metric as FM
import pytest
import schema


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute(schema.create_sql())
    c.execute("INSERT INTO core_metrics (period, revenue, cost_of_revenue, gross_profit,"
              " net_income, net_margin, eps, shares_outstanding, capex, units, currency)"
              " VALUES ('FY2021', 100.0, 20.0, 80.0, 10.0, 10.0, 0.5, 20.0, -3.0, 'millions', 'USD'),"
              "        ('FY2022', 143.6, 28.178, 114.2, 1.2, 4.9, 0.04, 33067716.0, 1.081, 'millions', 'USD')")
    c.execute("INSERT INTO kpis VALUES ('FY2022', 'ARR', 157.1, NULL), ('FY2021', 'ARR', 117.7, NULL)")
    yield c
    c.close()


def cell(con, period, col):
    return con.execute(f"SELECT {col} FROM core_metrics WHERE period = ?", [period]).fetchone()[0]


def corrections(con):
    return con.execute("SELECT target, period, col, old_value, new_value, source, op"
                       " FROM corrections ORDER BY rowid").fetchall()


SRC = "DCBO_40F_FY2022.txt:1234"


class TestSet:
    def test_set_updates_cell_and_records_old_value(self, con):
        recs = FM.apply_ops(con, [FM.Set("FY2022", {"revenue": 142.912, "net_income": 7.018})],
                            source=SRC, actor="test")
        assert cell(con, "FY2022", "revenue") == 142.912
        assert cell(con, "FY2022", "net_income") == 7.018
        assert len(recs) == 2
        assert corrections(con)[0] == ("core_metrics", "FY2022", "revenue", 143.6, 142.912, SRC, "set")

    def test_set_refuses_unknown_period_or_column(self, con):
        with pytest.raises(FM.FixError, match="period"):
            FM.apply_ops(con, [FM.Set("FY2099", {"revenue": 1.0})], source=SRC, actor="t")
        with pytest.raises(FM.FixError, match="column"):
            FM.apply_ops(con, [FM.Set("FY2022", {"banana": 1.0})], source=SRC, actor="t")

    def test_unchanged_value_is_not_recorded(self, con):
        recs = FM.apply_ops(con, [FM.Set("FY2022", {"revenue": 143.6})], source=SRC, actor="t")
        assert recs == []
        assert corrections(con) == []


class TestScale:
    def test_scale_column_across_all_periods(self, con):
        recs = FM.apply_ops(con, [FM.Scale(["shares_outstanding"], 1e-6, periods=None)],
                            source="shares stored absolute under units=millions", actor="t")
        assert cell(con, "FY2022", "shares_outstanding") == pytest.approx(33.067716)
        assert cell(con, "FY2021", "shares_outstanding") == pytest.approx(2e-5)
        assert len(recs) == 2
        assert {r["op"] for r in recs} == {"scale"}

    def test_scale_limited_to_named_periods_skips_nulls(self, con):
        con.execute("UPDATE core_metrics SET eps = NULL WHERE period = 'FY2021'")
        recs = FM.apply_ops(con, [FM.Scale(["eps"], 0.01, periods=["FY2021", "FY2022"])],
                            source="EPS printed in cents", actor="t")
        assert len(recs) == 1
        assert cell(con, "FY2022", "eps") == pytest.approx(0.0004)


class TestDerive:
    def test_derive_eps_from_net_income_and_shares(self, con):
        recs = FM.apply_ops(con, [FM.Derive("eps", "net_income / shares_outstanding", periods=None)],
                            source="restated share basis", actor="t")
        assert cell(con, "FY2021", "eps") == pytest.approx(0.5)     # unchanged, not recorded
        assert cell(con, "FY2022", "eps") == pytest.approx(1.2 / 33067716.0)
        assert [r["period"] for r in recs] == ["FY2022"]

    def test_derive_with_literal_and_precedence(self, con):
        FM.apply_ops(con, [FM.Derive("net_margin", "net_income / revenue * 100", periods=["FY2022"])],
                     source="Revenue is NTI", actor="t")
        assert cell(con, "FY2022", "net_margin") == pytest.approx(1.2 / 143.6 * 100)

    def test_derive_skips_rows_with_a_null_operand(self, con):
        con.execute("UPDATE core_metrics SET shares_outstanding = NULL WHERE period = 'FY2022'")
        recs = FM.apply_ops(con, [FM.Derive("eps", "net_income / shares_outstanding", periods=None)],
                            source="x", actor="t")
        assert recs == []

    def test_derive_rejects_anything_but_arithmetic(self, con):
        with pytest.raises(FM.FixError):
            FM.apply_ops(con, [FM.Derive("eps", "__import__('os')", periods=None)], source="x", actor="t")
        with pytest.raises(FM.FixError, match="column"):
            FM.apply_ops(con, [FM.Derive("eps", "banana / 2", periods=None)], source="x", actor="t")


class TestNullAndMove:
    def test_null_blanks_cells_and_records_them(self, con):
        recs = FM.apply_ops(con, [FM.Null(["net_margin"], periods=None)], source="recomputed later", actor="t")
        assert cell(con, "FY2022", "net_margin") is None
        assert len(recs) == 2
        assert recs[0]["new_value"] is None

    def test_move_core_cell_into_kpis(self, con):
        recs = FM.apply_ops(con, [FM.Move("FY2022", "shares_outstanding", "SharesAsPrinted", "shares")],
                            source="column shift", actor="t")
        assert cell(con, "FY2022", "shares_outstanding") is None
        assert con.execute("SELECT value, unit FROM kpis WHERE name='SharesAsPrinted'").fetchone() \
            == (33067716.0, "shares")
        ops = [r["op"] for r in recs]
        assert ops == ["move", "kpi"]


class TestKpis:
    def test_kpi_inserts_or_replaces_a_row(self, con):
        FM.apply_ops(con, [FM.Kpi("FY2025", "AISC", 1450.0, "AUD/oz")], source="PFS p.12", actor="t")
        FM.apply_ops(con, [FM.Kpi("FY2025", "AISC", 1500.0, "AUD/oz")], source="PFS p.12 rev", actor="t")
        rows = con.execute("SELECT value, unit FROM kpis WHERE name='AISC'").fetchall()
        assert rows == [(1500.0, "AUD/oz")]
        recs = corrections(con)
        assert recs[-1] == ("kpis:AISC", "FY2025", "value", 1450.0, 1500.0, "PFS p.12 rev", "kpi")

    def test_kpi_unit_default_fills_only_nulls(self, con):
        con.execute("INSERT INTO kpis VALUES ('FY2023', 'Customers', 3395, 'count')")
        recs = FM.apply_ops(con, [FM.KpiUnitDefault("USD millions")], source="units never tagged", actor="t")
        assert len(recs) == 2
        assert con.execute("SELECT DISTINCT unit FROM kpis WHERE name='ARR'").fetchall() == [("USD millions",)]
        assert con.execute("SELECT unit FROM kpis WHERE name='Customers'").fetchone() == ("count",)
        assert recs[0]["col"] == "unit:USD millions"


class TestReplay:
    def test_replay_reapplies_every_recorded_correction(self, con):
        recs = FM.apply_ops(con, [
            FM.Set("FY2022", {"revenue": 142.912}),
            FM.Scale(["shares_outstanding"], 1e-6, periods=None),
            FM.Null(["net_margin"], periods=["FY2021"]),
            FM.Move("FY2021", "capex", "CapexAsPrinted", "USD millions"),
            FM.Kpi("FY2025", "AISC", 1450.0, "AUD/oz"),
            FM.KpiUnitDefault("USD millions"),
        ], source=SRC, actor="t")
        after = con.execute("SELECT * FROM core_metrics ORDER BY period").fetchall()
        kpis_after = con.execute("SELECT * FROM kpis ORDER BY period, name").fetchall()

        # A fresh DB with the pre-fix values (what load_existing would build)
        fresh = duckdb.connect(":memory:")
        fresh.execute(schema.create_sql())
        fresh.execute("INSERT INTO core_metrics (period, revenue, cost_of_revenue, gross_profit,"
                      " net_income, net_margin, eps, shares_outstanding, capex, units, currency)"
                      " VALUES ('FY2021', 100.0, 20.0, 80.0, 10.0, 10.0, 0.5, 20.0, -3.0, 'millions', 'USD'),"
                      "        ('FY2022', 143.6, 28.178, 114.2, 1.2, 4.9, 0.04, 33067716.0, 1.081, 'millions', 'USD')")
        fresh.execute("INSERT INTO kpis VALUES ('FY2022', 'ARR', 157.1, NULL), ('FY2021', 'ARR', 117.7, NULL)")
        n = FM.replay(fresh, recs)
        assert n == len(recs)
        assert fresh.execute("SELECT * FROM core_metrics ORDER BY period").fetchall() == after
        assert fresh.execute("SELECT * FROM kpis ORDER BY period, name").fetchall() == kpis_after


def make_repo(tmp_path, ticker="SYN"):
    reports = tmp_path / "research" / ticker / "Reports"
    reports.mkdir(parents=True)
    c = duckdb.connect(str(reports / f"{ticker}.duckdb"))
    c.execute(schema.create_sql())
    c.execute("INSERT INTO core_metrics (period, revenue, units) VALUES ('FY2022', 143.6, 'millions')")
    c.close()
    return tmp_path


def run_main(monkeypatch, repo, *argv):
    monkeypatch.setattr(FM, "REPO", repo)
    monkeypatch.setattr(sys, "argv", ["fix_metric.py", *argv])
    return FM.main()


class TestCli:
    def test_dry_run_prints_plan_and_writes_nothing(self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        rc = run_main(monkeypatch, repo, "SYN", "--period", "FY2022", "--set", "revenue=142.912",
                      "--source", SRC, "--no-export")
        assert rc == 0
        out = capsys.readouterr().out
        assert "revenue" in out
        assert "143.6" in out
        assert "142.912" in out
        assert "dry run" in out.lower()
        c = duckdb.connect(str(repo / "research/SYN/Reports/SYN.duckdb"), read_only=True)
        assert c.execute("SELECT revenue FROM core_metrics").fetchone()[0] == 143.6
        assert c.execute("SELECT count(*) FROM corrections").fetchone()[0] == 0
        assert not (repo / "research/SYN/Reports/SYN_Corrections.jsonl").exists()

    def test_apply_writes_db_and_jsonl_mirror(self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        rc = run_main(monkeypatch, repo, "SYN", "--period", "FY2022", "--set", "revenue=142.912",
                      "--source", SRC, "--apply", "--no-export")
        assert rc == 0
        c = duckdb.connect(str(repo / "research/SYN/Reports/SYN.duckdb"), read_only=True)
        assert c.execute("SELECT revenue FROM core_metrics").fetchone()[0] == 142.912
        assert c.execute("SELECT period_type, fiscal_year FROM core_metrics").fetchone() == ("FY", 2022)
        lines = (repo / "research/SYN/Reports/SYN_Corrections.jsonl").read_text().splitlines()
        rec = json.loads(lines[0])
        assert rec["op"] == "set"
        assert rec["old_value"] == 143.6
        assert rec["new_value"] == 142.912
        assert rec["source"] == SRC

    def test_source_is_mandatory(self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        rc = run_main(monkeypatch, repo, "SYN", "--period", "FY2022", "--set", "revenue=1")
        assert rc == 2
        assert "--source" in capsys.readouterr().err

    def test_set_without_period_is_an_error(self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        rc = run_main(monkeypatch, repo, "SYN", "--set", "revenue=1", "--source", "x")
        assert rc == 2

    def test_bad_period_reports_and_exits_1(self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        rc = run_main(monkeypatch, repo, "SYN", "--period", "FY2099", "--set", "revenue=1",
                      "--source", "x", "--apply", "--no-export")
        assert rc == 1
        assert "FY2099" in capsys.readouterr().err

    def test_parse_argv_builds_every_op_kind(self):
        ops = FM.parse_ops([
            "--period", "FY2022", "--set", "revenue=1", "cost_of_revenue=2",
            "--scale", "eps", "0.01", "--derive", "net_margin=net_income/revenue*100",
            "--null", "gross_margin", "--move", "capex->kpis:CapexAsPrinted",
            "--unit", "USD millions", "--kpi", "AISC=1450", "--kpi-unit-default", "USD",
        ])
        kinds = [type(o).__name__ for o in ops]
        assert kinds == ["Set", "Scale", "Derive", "Null", "Move", "Kpi", "KpiUnitDefault"]
        assert ops[0].values == {"revenue": 1.0, "cost_of_revenue": 2.0}
        assert ops[1].periods == ["FY2022"]
        assert ops[4].unit == "USD millions"
        assert ops[5].unit == "USD millions"

    def test_periods_all_means_no_filter(self):
        ops = FM.parse_ops(["--periods", "all", "--scale", "eps", "0.01"])
        assert ops[0].periods is None


class TestKpiUnitByName:
    def test_kpi_unit_sets_only_that_name_where_null(self, con):
        con.execute("INSERT INTO kpis VALUES ('FY2023', 'ACV', 46.3, NULL),"
                    " ('FY2023', 'Customers', 3395, 'count')")
        recs = FM.apply_ops(con, [FM.KpiUnit("ARR", "USD millions"), FM.KpiUnit("ACV", "USD thousands")],
                            source="units never tagged", actor="t")
        assert len(recs) == 3
        assert con.execute("SELECT DISTINCT unit FROM kpis WHERE name='ARR'").fetchall() == [("USD millions",)]
        assert con.execute("SELECT unit FROM kpis WHERE name='ACV'").fetchone() == ("USD thousands",)
        assert con.execute("SELECT unit FROM kpis WHERE name='Customers'").fetchone() == ("count",)
        assert recs[0]["op"] == "kpi_unit"

    def test_parse_kpi_unit_flag(self):
        ops = FM.parse_ops(["--kpi-unit", "ARR=USD millions", "--kpi-unit", "Customers=count"])
        assert [type(o).__name__ for o in ops] == ["KpiUnit", "KpiUnit"]
        assert (ops[0].name, ops[0].unit) == ("ARR", "USD millions")
