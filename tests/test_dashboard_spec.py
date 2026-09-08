"""Tests for dashboard_spec.py: a deterministic default DashboardSpec.

The dashboard-generator agent cost $1.64/run (14% of a ticker) and spent
its 28 turns reading OTHER tickers' dashboards to decide which of ~10
standard charts to include. The choice is a function of which CSV columns
are populated and what kind of business it is; a script makes it for free
and the agent, if spawned at all, only tweaks. Every fixture is synthetic.
"""

import json
import sys

import build_dashboard
import dashboard_spec as DS

HEAD = ["Period", "Revenue", "GrossProfit", "GrossMargin", "OperatingIncome",
        "OperatingMargin", "EBITDA", "NetIncome", "NetMargin", "EPS",
        "OperatingCashFlow", "CapEx", "FreeCashFlow", "ShareholdersEquity",
        "TotalAssets", "TotalLiabilities", "TotalDebt", "CashAndEquivalents",
        "SharesOutstanding", "StockBasedComp", "DividendPerShare", "Units", "Currency"]


def csv_text(extra_cols=(), years=(2021, 2022, 2023, 2024), blank=(), rows_extra=None):
    cols = HEAD[:-2] + list(extra_cols) + HEAD[-2:]
    lines = [",".join(cols)]
    for i, y in enumerate(years):
        base = {"Period": f"FY{y}", "Revenue": 100 + 10 * i, "GrossProfit": 60 + 6 * i,
                "GrossMargin": 60.0, "OperatingIncome": 10 + i, "OperatingMargin": 10.0,
                "EBITDA": 15 + i, "NetIncome": 8 + i, "NetMargin": 8.0, "EPS": 0.4 + 0.05 * i,
                "OperatingCashFlow": 12 + i, "CapEx": 3, "FreeCashFlow": 9 + i,
                "ShareholdersEquity": 50 + 5 * i, "TotalAssets": 90 + 5 * i,
                "TotalLiabilities": 40, "TotalDebt": 10, "CashAndEquivalents": 20 + i,
                "SharesOutstanding": 20.0, "StockBasedComp": 1.0, "DividendPerShare": 0.1,
                "Units": "millions", "Currency": "NZD"}
        for c in extra_cols:
            base[c] = (rows_extra or {}).get(c, 1000 + i)
        for c in blank:
            base[c] = ""
        lines.append(",".join(str(base.get(c, "")) for c in cols))
    return "\n".join(lines) + "\n"


def make_ticker_files(tmp_path, ticker="SYN", csv=None, analysis=None, info=None, dcf=None):
    reports = tmp_path / "research" / ticker / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{ticker}_Metrics.csv").write_text(csv or csv_text())
    (reports / f"{ticker}_Analysis.json").write_text(json.dumps(
        analysis or {"company_name": "Synthetic Co", "sector": "Industrial Manufacturing",
                     "overview": "Makes things."}))
    if info is not None:
        (tmp_path / "research" / ticker / "info.json").write_text(json.dumps(info))
    if dcf is not None:
        (reports / f"{ticker}_DCF.json").write_text(json.dumps(dcf))
    return tmp_path


def chart_ids(spec):
    return [c["id"] for s in spec["sections"] for c in s["charts"]]


def columns_used(spec):
    out = set()
    for k in spec["kpis"]:
        if "column" in k:
            out.add(k["column"])
    for s in spec["sections"]:
        for c in s["charts"]:
            for se in c["series"]:
                if "column" in se:
                    out.add(se["column"])
    return out


class TestUniversalSpec:
    def test_operating_company_gets_the_universal_set_and_validates(self, tmp_path):
        repo = make_ticker_files(tmp_path)
        spec = DS.default_spec("SYN", repo)
        ids = chart_ids(spec)
        for wanted in ("revenueChart", "marginsChart", "fcfChart", "cashDebtChart", "sbcChart"):
            assert wanted in ids
        cols = set(build_dashboard.read_csv(csv_text())[0])
        build_dashboard.validate_spec(spec, cols)         # the builder's own gate
        assert 6 <= len(spec["kpis"]) <= 8
        assert len(json.dumps(spec)) < 12_000

    def test_descriptor_currency_and_units_from_the_inputs(self, tmp_path):
        repo = make_ticker_files(tmp_path, info={"fiscal_year_end": "06-30", "sector": "Widgets"},
                                 dcf={"inputs": {"currency": "NZD", "quote_currency": "NZD"},
                                      "valuation_date": "2026-09-01"})
        spec = DS.default_spec("SYN", repo)
        assert "Widgets" in spec["descriptor"]
        assert "30 June" in spec["descriptor"]
        assert "NZD" in spec["descriptor"]
        assert spec["currency"] == "NZ$"
        assert spec["units"] == "m"

    def test_charts_whose_columns_are_blank_are_dropped(self, tmp_path):
        repo = make_ticker_files(tmp_path, csv=csv_text(blank=("GrossMargin", "OperatingMargin",
                                                                "NetMargin", "StockBasedComp")))
        spec = DS.default_spec("SYN", repo)
        ids = chart_ids(spec)
        assert "marginsChart" not in ids
        assert "sbcChart" in ids             # SharesOutstanding still carries it
        assert "StockBasedComp" not in columns_used(spec)

    def test_series_needs_three_populated_rows(self, tmp_path):
        repo = make_ticker_files(tmp_path, csv=csv_text(years=(2023, 2024)))
        spec = DS.default_spec("SYN", repo)
        assert "revenueChart" not in chart_ids(spec)
        assert spec["sections"] == [] or all(s["charts"] for s in spec["sections"])

    def test_every_help_key_is_present_and_company_substituted(self, tmp_path):
        repo = make_ticker_files(tmp_path)
        spec = DS.default_spec("SYN", repo)
        for s in spec["sections"]:
            for c in s["charts"]:
                assert c["help"] in spec["metric_descriptions"]
        text = json.dumps(spec["metric_descriptions"])
        assert "{Company}" not in text
        assert "Synthetic Co" in text

    def test_owner_fcf_series_only_when_the_dcf_has_the_history(self, tmp_path):
        repo = make_ticker_files(tmp_path, dcf={"inputs": {"last_fcf": 9.0}})
        spec = DS.default_spec("SYN", repo)
        fcf = next(c for s in spec["sections"] for c in s["charts"] if c["id"] == "fcfChart")
        assert not any("dcf_path" in se for se in fcf["series"])
        repo2 = make_ticker_files(tmp_path / "b", dcf={"inputs": {"last_fcf": 9.0},
                                  "historical_growth": {"owner_fcf_history": {"FY2024": 8.0}}})
        spec2 = DS.default_spec("SYN", repo2)
        fcf2 = next(c for s in spec2["sections"] for c in s["charts"] if c["id"] == "fcfChart")
        assert any(se.get("dcf_path") == "historical_growth.owner_fcf_history" for se in fcf2["series"])

    def test_model_approach_note_only_when_a_fix_was_applied(self, tmp_path):
        repo = make_ticker_files(tmp_path, dcf={"inputs": {"last_fcf": 9.0},
                                                "sanity_check": {"passed": False,
                                                                 "fix_applied": "Switched to mid-cycle FCF"}})
        assert "mid-cycle" in DS.default_spec("SYN", repo)["dcf"]["model_approach_note"]
        repo2 = make_ticker_files(tmp_path / "b", dcf={"inputs": {"last_fcf": 9.0},
                                                       "sanity_check": {"passed": True}})
        assert "model_approach_note" not in DS.default_spec("SYN", repo2)["dcf"]


class TestBusinessTemplates:
    def test_ecommerce_gets_gmv_when_the_column_is_populated(self, tmp_path):
        repo = make_ticker_files(tmp_path, csv=csv_text(extra_cols=("GMV", "TakeRate", "ActiveCustomers")),
                                 analysis={"company_name": "Shop Co", "sector": "E-commerce marketplace"})
        spec = DS.default_spec("SYN", repo)
        ids = chart_ids(spec)
        assert "gmvChart" in ids
        assert "arrChart" not in ids
        assert "GMV" in columns_used(spec)

    def test_saas_template_drops_absent_kpis_but_keeps_present_ones(self, tmp_path):
        repo = make_ticker_files(tmp_path, csv=csv_text(extra_cols=("ARR", "Customers")),
                                 analysis={"company_name": "Cloud Co", "sector": "Software - SaaS"})
        spec = DS.default_spec("SYN", repo)
        ids = chart_ids(spec)
        assert "arrChart" in ids
        assert "customersChart" in ids
        assert "nrrChart" not in ids            # NetRevenueRetention absent
        assert any(k.get("column") == "ARR" for k in spec["kpis"])

    def test_prerevenue_miner_has_no_revenue_chart(self, tmp_path):
        csv = csv_text(extra_cols=("AISC",), blank=("Revenue", "GrossProfit", "GrossMargin", "OperatingMargin",
                                                     "NetMargin", "EBITDA", "DividendPerShare"),
                       rows_extra={"AISC": 1660})
        repo = make_ticker_files(tmp_path, csv=csv, analysis={"company_name": "Dig Co",
                                                             "sector": "Gold Mining / Exploration"},
                                 dcf={"model": "risked-project-npv", "inputs": {"last_fcf": -19.0}})
        spec = DS.default_spec("SYN", repo)
        ids = chart_ids(spec)
        assert "revenueChart" not in ids
        assert "cashDebtChart" in ids
        assert any(k.get("column") == "AISC" for k in spec["kpis"])

    def test_reit_template_uses_affo_and_nta(self, tmp_path):
        repo = make_ticker_files(tmp_path, csv=csv_text(extra_cols=("AFFO", "NTAPerShare", "Occupancy")),
                                 dcf={"model": "AFFO capitalization at cost of equity", "inputs": {}})
        spec = DS.default_spec("SYN", repo)
        assert "affoChart" in chart_ids(spec)
        assert "ntaChart" in chart_ids(spec)

    def test_every_template_validates_against_a_full_csv(self, tmp_path):
        """Templates are data; a typo in a column name must fail here, not
        on a ticker at 2 a.m."""
        for name, tpl in DS.load_templates().items():
            cols = set(HEAD) | DS.template_columns(tpl)
            spec = DS.assemble(tpl if name == "universal" else DS.load_templates()["universal"],
                               tpl, cols, has=lambda c: True, company="X Co", dcf=None)
            build_dashboard.validate_spec(spec, cols)


def run_main(monkeypatch, repo, *argv):
    monkeypatch.setattr(DS, "REPO", repo)
    monkeypatch.setattr(sys, "argv", ["dashboard_spec.py", *argv])
    return DS.main()


class TestCli:
    def test_writes_spec_and_refuses_to_overwrite_without_force(self, tmp_path, monkeypatch, capsys):
        repo = make_ticker_files(tmp_path)
        out = repo / "research/SYN/Reports/SYN_DashboardSpec.json"
        assert run_main(monkeypatch, repo, "SYN") == 0
        assert out.exists()
        first = out.read_text()
        out.write_text(first.replace("Synthetic Co", "Edited by agent"))
        assert run_main(monkeypatch, repo, "SYN") == 1
        assert "exists" in capsys.readouterr().err
        assert "Edited by agent" in out.read_text()
        assert run_main(monkeypatch, repo, "SYN", "--force") == 0
        assert "Edited by agent" not in out.read_text()

    def test_stdout_prints_without_writing(self, tmp_path, monkeypatch, capsys):
        repo = make_ticker_files(tmp_path)
        assert run_main(monkeypatch, repo, "SYN", "--stdout") == 0
        assert '"descriptor"' in capsys.readouterr().out
        assert not (repo / "research/SYN/Reports/SYN_DashboardSpec.json").exists()

    def test_missing_csv_is_an_error(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "research" / "NOPE" / "Reports").mkdir(parents=True)
        assert run_main(monkeypatch, tmp_path, "NOPE") == 1
