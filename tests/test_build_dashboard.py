"""Tests for build_dashboard.py: deterministic dashboard rendering from a Spec.

The dashboard-generator agent used to emit ~1,400 lines of boilerplate HTML,
CSS and JS per ticker (54k output tokens on TPW.AX) and broke it often enough
that a verify gate exists. Now the agent writes a few-KB
`{T}_DashboardSpec.json` and this script renders the page from
scripts/templates/dashboard.html. Fixtures under tests/fixtures/dashboard/;
tests never read live research/ files.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

import build_dashboard as bd
import pytest

FIX = Path(__file__).parent / "fixtures" / "dashboard"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def spec():
    return load("TEST_DashboardSpec.json")


@pytest.fixture
def analysis():
    return load("TEST_Analysis.json")


@pytest.fixture
def dcf():
    return load("TEST_DCF.json")


@pytest.fixture
def csv_text():
    return (FIX / "TEST_Metrics.csv").read_text()


@pytest.fixture
def html(spec, analysis, dcf, csv_text):
    return bd.render("TEST", spec, csv_text, analysis, dcf)


def inline_script(page):
    m = re.search(r"<script>(.*)</script>\s*</body>", page, re.DOTALL)
    assert m, "no inline script block"
    return m.group(1)


class TestCsv:
    def test_rows_sorted_chronologically_via_periods(self):
        text = "Period,Revenue\nFY2025,3\nH1 FY2024,1\nFY2024,2\n"
        cols, rows = bd.read_csv(text)
        assert cols == ["Period", "Revenue"]
        assert [r["Period"] for r in rows] == ["H1 FY2024", "FY2024", "FY2025"]

    def test_annual_rows_use_periods_is_annual_not_startswith(self):
        text = "Period,Revenue\nFY2017-15mo,1\nFY2018,2\nH1 FY2019,3\n"
        _, rows = bd.read_csv(text)
        assert [r["Period"] for r in bd.annual_rows(rows)] == ["FY2018"]

    def test_interim_kind(self):
        _, half = bd.read_csv("Period,Revenue\nFY2024,1\nH1 FY2025,2\n")
        _, quarter = bd.read_csv("Period,Revenue\nFY2024,1\nQ1 2025,2\n")
        _, annual = bd.read_csv("Period,Revenue\nFY2024,1\nFY2025,2\n")
        assert bd.interim_kind(half) == "half"
        assert bd.interim_kind(quarter) == "quarterly"
        assert bd.interim_kind(annual) is None

    def test_short_labels(self):
        assert bd.short_label("FY2026") == "FY26"
        assert bd.short_label("H1 FY2026") == "H1'26"
        assert bd.short_label("H1-2026") == "H1'26"
        assert bd.short_label("Q3 2025") == "Q3'25"
        assert bd.short_label("Q3-2025") == "Q3'25"

    def test_series_blank_is_null(self):
        _, rows = bd.read_csv("Period,Revenue,EBITDA\nFY2024,10,\nFY2025,12,3\n")
        assert bd.series(rows, "EBITDA") == [None, 3.0]


class TestDerive:
    def test_yoy_uses_same_period_type_prior_year(self):
        _, rows = bd.read_csv(
            "Period,Revenue\nFY2023,100\nH1 FY2024,60\nFY2024,130\nH1 FY2025,75\nFY2025,160\n")
        fy = bd.annual_rows(rows)
        assert bd.derive(fy, "yoy:Revenue") == [None, 30.0, pytest.approx(23.0769, abs=1e-3)]
        halves = bd.interim_rows(rows)
        assert bd.derive(halves, "yoy:Revenue") == [None, 25.0]

    def test_ratio_is_percent_and_null_on_missing(self):
        _, rows = bd.read_csv("Period,EBITDA,Revenue\nFY2024,10,100\nFY2025,,120\n")
        assert bd.derive(rows, "ratio:EBITDA/Revenue") == [10.0, None]

    def test_dcf_path_series_keyed_by_period(self, dcf):
        _, rows = bd.read_csv("Period,Revenue\nFY2023,1\nFY2024,2\nFY2026,3\n")
        assert bd.dcf_series(rows, dcf, "historical_growth.owner_fcf_history") == [7.0, -3.0, None]

    def test_unknown_derive_rejected(self):
        with pytest.raises(bd.SpecError):
            bd.derive([], "median:Revenue")

    def test_per_is_a_plain_ratio_not_a_percent(self):
        """Unit economics are per-unit, not percentages.

        `ratio:` hardcodes x100, so marketing spend / customers came out
        100x too large and labelled as a share of something.
        """
        _, rows = bd.read_csv(
            "Period,MarketingExpense,ActiveCustomers\nFY2024,100,4\n")
        assert bd.derive(rows, "per:MarketingExpense/ActiveCustomers") == [25.0]
        assert bd.derive(rows, "ratio:MarketingExpense/ActiveCustomers") == [2500.0]

    def test_per_null_on_missing_or_zero_denominator(self):
        _, rows = bd.read_csv(
            "Period,A,B\nFY2024,10,0\nFY2025,,5\nFY2026,10,\n")
        assert bd.derive(rows, "per:A/B") == [None, None, None]

    def test_per_needs_both_operands(self):
        with pytest.raises(bd.SpecError):
            bd.derive([], "per:MarketingExpense")


class TestSeriesScale:
    """`scale` reconciles operands recorded on different scales.

    Marketing spend is stored in millions but a customer count is a raw
    integer, so A$m / customers is ~0.00004 and rounds to zero on the axis.
    Scaling is a display concern, kept out of `derive` so the arithmetic
    stays dumb.
    """

    def test_scale_multiplies_a_derived_series(self):
        _, rows = bd.read_csv(
            "Period,MarketingExpense,ActiveCustomers\nFY2024,101.242,1333000\n")
        s = {"label": "per", "derive": "per:MarketingExpense/ActiveCustomers",
             "scale": 1e6}
        assert bd._series_values(s, rows, None) == [pytest.approx(75.95, abs=0.01)]

    def test_scale_multiplies_a_plain_column(self):
        _, rows = bd.read_csv("Period,Revenue\nFY2024,1.5\n")
        assert bd._series_values(
            {"label": "r", "column": "Revenue", "scale": 1000}, rows, None) == [1500.0]

    def test_absent_scale_leaves_values_untouched(self):
        _, rows = bd.read_csv("Period,Revenue\nFY2024,1.5\n")
        assert bd._series_values(
            {"label": "r", "column": "Revenue"}, rows, None) == [1.5]

    def test_scale_preserves_nulls(self):
        _, rows = bd.read_csv("Period,Revenue\nFY2024,\n")
        assert bd._series_values(
            {"label": "r", "column": "Revenue", "scale": 1e6}, rows, None) == [None]


class TestSpecValidation:
    def test_valid_spec_passes(self, spec):
        bd.validate_spec(spec, {"Revenue", "GrossMargin", "EBITDA", "FreeCashFlow",
                                "CashAndEquivalents"})

    def test_unknown_column_rejected(self, spec):
        spec["kpis"][0]["column"] = "Revenu"
        with pytest.raises(bd.SpecError, match="Revenu"):
            bd.validate_spec(spec, {"Revenue"})

    def test_per_derive_validates_both_operands(self, spec):
        spec["sections"][0]["charts"][0]["series"][0] = {
            "label": "per", "derive": "per:MarketingExpense/Nope"}
        with pytest.raises(bd.SpecError, match="Nope"):
            bd.validate_spec(spec, {"Revenue", "GrossMargin", "EBITDA",
                                    "FreeCashFlow", "CashAndEquivalents",
                                    "MarketingExpense"})

    def test_help_key_must_exist(self, spec):
        spec["sections"][0]["charts"][0]["help"] = "nope"
        with pytest.raises(bd.SpecError, match="nope"):
            bd.validate_spec(spec, {"Revenue", "GrossMargin", "EBITDA", "FreeCashFlow",
                                    "CashAndEquivalents"})

    def test_duplicate_chart_id_rejected(self, spec):
        spec["sections"][1]["charts"][0]["id"] = "revenueChart"
        with pytest.raises(bd.SpecError, match="revenueChart"):
            bd.validate_spec(spec, {"Revenue", "GrossMargin", "EBITDA", "FreeCashFlow",
                                    "CashAndEquivalents"})

    def test_missing_top_level_keys_rejected(self):
        with pytest.raises(bd.SpecError, match="missing"):
            bd.validate_spec({"kpis": []}, set())


class TestEmbedding:
    def test_csv_embedded_verbatim(self, html, csv_text):
        assert "const csvData = `" + csv_text.strip() + "`;" in html
        assert "fetch(" not in html

    def test_analysis_and_dcf_embedded_with_all_keys(self, html, analysis, dcf):
        script = inline_script(html)
        a = re.search(r"const analysis = (\{.*?\});\n", script, re.DOTALL).group(1)
        d = re.search(r"const dcfData = (\{.*?\});\n", script, re.DOTALL).group(1)
        assert set(json.loads(a)) == set(analysis)
        assert set(json.loads(d)) == set(dcf)

    def test_title_and_descriptor(self, html):
        assert "<title>Test Co (TEST) Financial Dashboard</title>" in html
        assert "Widget maker &middot; FY ends 30 June" in html

    def test_only_chartjs_is_external(self, html):
        srcs = re.findall(r'src="([^"]+)"', html)
        assert srcs == ["https://cdn.jsdelivr.net/npm/chart.js"]


class TestKpis:
    def test_yoy_change_computed_from_latest_two_fy_rows(self, html):
        # FY2025 revenue 160 vs FY2024 130 -> +23.1%
        assert "A$160.0m" in html
        assert "+23.1% YoY" in html
        assert "FY target A$180m" in html

    def test_pct_format(self, html):
        assert "43.0%" in html

    def test_dcf_path_kpi(self, html):
        assert "A$10.5m" in html

    def test_literal_kpi(self, html):
        assert "~3%" in html
        assert "up from 2%" in html

    def test_negative_yoy_is_negative_class(self, spec, analysis, dcf):
        csv = "Period,Revenue\nFY2024,100\nFY2025,90\n"
        spec["kpis"] = [{"label": "Revenue", "column": "Revenue", "format": "money", "change": "yoy"}]
        spec["sections"] = []
        spec["metric_descriptions"] = {}
        page = bd.render("TEST", spec, csv, analysis, dcf)
        assert '<div class="kpi-change negative">-10.0% YoY</div>' in page


class TestCharts:
    def test_sections_and_cards_rendered(self, html):
        assert "Growth &amp; Profitability" in html
        assert '<p class="section-subtitle">Margins are pre-reclass.</p>' in html
        for cid in ("revenueChart", "gmChart", "ebitdaMarginChart", "fcfChart", "cashChart"):
            assert f'<canvas id="{cid}"></canvas>' in html
        assert '<div class="chart-annotation">Owner FCF from the DCF.</div>' in html

    def test_log_toggle_only_for_positive_absolute_series(self, html):
        assert "toggleLogScale('revenueChart'" in html
        assert "toggleLogScale('cashChart'" in html
        # percent charts never get one
        assert "toggleLogScale('gmChart'" not in html
        assert "toggleLogScale('ebitdaMarginChart'" not in html
        # FCF has a -2 (FY2024) and owner FCF -3: Chart.js log axes cannot plot it
        assert "toggleLogScale('fcfChart'" not in html

    def test_interim_toggle_default_annual_and_respects_opt_out(self, html):
        assert ("<button class=\"view-toggle active\" onclick=\"setPeriodView('revenueChart', "
                "'annual', this)\">FY</button>") in html
        assert "setPeriodView('revenueChart', 'interim', this)\">H1/H2</button>" in html
        assert "setPeriodView('fcfChart'" not in html

    def test_no_interim_toggle_when_csv_is_annual_only(self, spec, analysis, dcf):
        csv = ("Period,Revenue,GrossMargin,EBITDA,FreeCashFlow,CashAndEquivalents\n"
               "FY2024,100,40,10,5,50\nFY2025,120,41,12,6,60\n")
        page = bd.render("TEST", spec, csv, analysis, dcf)
        assert "setPeriodView(" not in page.split("<script>")[0]

    def test_quarterly_toggle_label(self, spec, analysis, dcf):
        csv = ("Period,Revenue,GrossMargin,EBITDA,FreeCashFlow,CashAndEquivalents\n"
               "FY2024,100,40,10,5,50\nQ1 2025,30,41,3,1,55\nQ1-2026,35,41,3,1,55\n")
        page = bd.render("TEST", spec, csv, analysis, dcf)
        assert "'interim', this)\">Q</button>" in page
        cd = bd.chart_data(spec, *bd.read_csv(csv)[1:], dcf)
        assert cd["revenueChart"]["interim"]["labels"] == ["Q1'25", "Q1'26"]

    def test_chart_data_series(self, spec, csv_text, dcf):
        _, rows = bd.read_csv(csv_text)
        cd = bd.chart_data(spec, rows, dcf)
        assert cd["revenueChart"]["annual"]["labels"] == ["FY23", "FY24", "FY25"]
        assert cd["revenueChart"]["annual"]["data"][0] == [100.0, 130.0, 160.0]
        assert cd["revenueChart"]["interim"]["labels"] == ["H1'24", "H1'25"]
        assert cd["fcfChart"]["annual"]["data"][1] == [7.0, -3.0, 10.5]
        assert "interim" not in cd["fcfChart"]
        assert cd["ebitdaMarginChart"]["annual"]["data"][0][0] == 10.0

    def test_metric_descriptions_embedded(self, html):
        assert '"grossMargin": {' in html
        assert '"title": "Gross Margin"' in html


class TestDcfSection:
    def test_dcf_ids_present(self, html):
        for i in ("headerValuation", "dcfWeighted", "dcfWeightedUpside", "dcfWeightedSublabel",
                  "dcfIV", "dcfEntry", "dcfCurrent", "growthSlider", "waccSlider",
                  "terminalSlider", "sensitivityMatrix", "growthGrid", "assumptionGrid"):
            assert f'id="{i}"' in html, i
        assert '<div class="dcf-card highlight">' in html
        assert html.index('id="dcfWeighted"') < html.index('id="dcfIV"')
        assert 'href="TEST_DCF_Model.xlsx"' in html

    def test_spec_dcf_labels(self, html):
        assert "Base Owner FCF (FY2025)" in html
        assert "Post-result close" in html

    def test_slider_ranges_hold_every_scenario_default(self, html):
        g = re.search(r'id="growthSlider" min="([-\d.]+)" max="([-\d.]+)" value="([-\d.]+)" step="0.1"',
                      html)
        assert g is not None
        assert float(g.group(1)) <= 4
        assert float(g.group(2)) >= 18
        assert float(g.group(3)) == 12  # base growth_rates[0], not selected_growth_rate
        w = re.search(r'id="waccSlider" min="([-\d.]+)" max="([-\d.]+)" value="([\d.]+)" step="0.05"', html)
        assert w is not None
        assert float(w.group(1)) <= 9.5
        assert float(w.group(2)) >= 11
        assert float(w.group(3)) == 10

    def test_no_dcf_omits_section_and_link_but_page_still_renders(self, spec, analysis, csv_text):
        page = bd.render("TEST", spec, csv_text, analysis, None)
        assert "const dcfData = null;" in page
        assert 'id="dcfWeighted"' not in page
        assert "DCF_Model.xlsx" not in page
        assert 'id="headerValuation"' in page
        assert '<canvas id="revenueChart"></canvas>' in page
        # a dcf_path KPI degrades to a dash rather than crashing
        assert "Owner FCF" in page


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestNode:
    def test_inline_script_parses(self, html, tmp_path):
        js = tmp_path / "dash.js"
        js.write_text(inline_script(html))
        r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr

    def test_dcf_math_reproduces_json_at_defaults_and_on_every_tab(self, html, dcf, tmp_path):
        """The verify gate, executed headlessly: at scenario defaults the cards
        show the JSON's own numbers, and the weighted IV never moves on a tab."""
        js = tmp_path / "dash.js"
        harness = bd.node_harness(inline_script(html))
        js.write_text(harness)
        r = subprocess.run(["node", str(js)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        for sc in ("base", "bull", "bear"):
            assert out[sc]["iv"] == pytest.approx(dcf["valuation"][sc]["intrinsic_value"], abs=0.005)
            assert out[sc]["entry"] == pytest.approx(dcf["entry_price"][sc]["entry_price"], abs=0.005)
            assert out[sc]["weighted"] == pytest.approx(dcf["probability_weighted"]["weighted_iv"], abs=0.005)
            assert out[sc]["slider_ok"] is True
        assert out["moved"]["weighted"] != pytest.approx(dcf["probability_weighted"]["weighted_iv"], abs=0.005)


class TestValuationEngine:
    """The sliders re-run the analyst's model when assumptions carry the
    component fields (TPW.AX: 10-year component build), and fall back to the
    anchored scaler when they do not (the TEST fixture)."""

    def run(self, html, tmp_path):
        js = tmp_path / "dash.js"
        js.write_text(bd.node_harness(inline_script(html)))
        r = subprocess.run(["node", str(js)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def test_component_model_validates_and_drives_sliders(self, spec, analysis, csv_text, tmp_path):
        dcf = load("TPW_DCF.json")
        html = bd.render("TPW.AX", spec, csv_text, analysis, dcf)
        out = self.run(html, tmp_path)
        for sc in ("base", "bull", "bear"):
            assert out["engine"][sc]["family"] == "component"
            assert out["engine"][sc]["ok"] is True
            assert out["engine"][sc]["iv"] == pytest.approx(dcf["valuation"][sc]["intrinsic_value"], rel=0.015)
            assert out[sc]["iv"] == pytest.approx(dcf["valuation"][sc]["intrinsic_value"], abs=0.005)
        # +5pp year-1 growth on a company with positive later-year owner FCF raises value
        assert out["moved"]["iv"] > dcf["valuation"]["base"]["intrinsic_value"]
        assert 'id="dcfEngineNote"' in html      # the note initDCF fills at runtime

    def test_sum_of_parts_model_validates_and_drives_sliders(self, spec, analysis, csv_text, tmp_path):
        """A DCF whose value is an OpCo FCF leg PLUS an independently
        discounted embedded-lender leg (SE: Monee, valued on distributable
        earnings at its own cost of equity) cannot be reproduced by the
        single-stream `component` family -- it undershoots by 15-30% and,
        because the fallback is silent, the sliders would contradict the
        cards above them. The engine must pick `sum-of-parts` and reproduce
        both the IV and the entry price."""
        dcf = load("SE_DCF.json")
        html = bd.render("SE", spec, csv_text, analysis, dcf)
        out = self.run(html, tmp_path)
        for sc in ("base", "bull", "bear"):
            assert out["engine"][sc]["family"] == "sum-of-parts"
            assert out["engine"][sc]["ok"] is True
            assert out["engine"][sc]["iv"] == pytest.approx(dcf["valuation"][sc]["intrinsic_value"], abs=0.01)
            # The cards render to display precision (3sf), so they are checked
            # loosely; the engine assertions above are the exact reproduction.
            assert out[sc]["iv"] == pytest.approx(dcf["valuation"][sc]["intrinsic_value"], rel=0.005)
            assert out[sc]["entry"] == pytest.approx(dcf["entry_price"][sc]["entry_price"], rel=0.005)
        # The lender leg is deliberately NOT driven by the OpCo growth slider,
        # but the OpCo leg is: +5pp year-1 growth must still raise the IV.
        assert out["moved"]["iv"] > dcf["valuation"]["base"]["intrinsic_value"]

    def test_float_carrying_model_validates_and_drives_sliders(self, spec, analysis, csv_text, tmp_path):
        """A DCF whose equity bridge adds a separately-valued float stream
        (ADYEN.AS: merchant settlement float, valued on its own interest
        spread rather than driven by the operating growth path) cannot be
        reproduced by the plain `component` bridge -- the float is a real
        term the generic engine has no slot for, so the engine undershoots
        by 4-7% and silently falls back to the 5-year toy. That fallback is
        exactly the horizon error this ticker's DCF exists to correct, so
        the sliders would contradict the cards above them.
        """
        dcf = load("ADYEN_DCF.json")
        html = bd.render("ADYEN.AS", spec, csv_text, analysis, dcf)
        out = self.run(html, tmp_path)
        for sc in ("base", "bull"):
            assert out["engine"][sc]["family"] == "component"
            assert out["engine"][sc]["ok"] is True, f"{sc} fell back to the toy model"
            assert out["engine"][sc]["iv"] == pytest.approx(
                dcf["valuation"][sc]["intrinsic_value"], rel=0.015)
        # Every scenario's CARD shows the analyst's stored number regardless of
        # whether its engine validated -- that is the anchoring contract.
        for sc in ("base", "bull", "bear"):
            assert out[sc]["iv"] == pytest.approx(
                dcf["valuation"][sc]["intrinsic_value"], rel=0.005)
        # The float leg is not driven by the operating growth slider, but the
        # operating leg is: +5pp year-1 growth must still raise the IV.
        assert out["moved"]["iv"] > dcf["valuation"]["base"]["intrinsic_value"]

    def test_float_stream_is_added_to_the_equity_bridge(self, spec, analysis, csv_text, tmp_path):
        """Without the float term the component engine undershoots by ~6% and
        every scenario silently falls back. Deleting it from the fixture must
        break validation -- that is what proves the term is doing the work.
        """
        dcf = load("ADYEN_DCF.json")
        for sc in ("base", "bull", "bear"):
            dcf["valuation"][sc].pop("merchant_float_value", None)
        html = bd.render("ADYEN.AS", spec, csv_text, analysis, dcf)
        out = self.run(html, tmp_path)
        assert all(out["engine"][sc]["ok"] is False for sc in ("base", "bull", "bear"))

    def test_missing_component_fields_fall_back_to_scaler(self, html, dcf, tmp_path):
        out = self.run(html, tmp_path)
        assert all(out["engine"][sc]["ok"] is False for sc in ("base", "bull", "bear"))
        assert out["base"]["iv"] == pytest.approx(dcf["valuation"]["base"]["intrinsic_value"], abs=0.005)

    def test_build_reports_engine_status(self, make_ticker, monkeypatch, capsys):
        d = make_ticker("TPW.AX")
        for src, dst in (("TEST_Metrics.csv", "TPW.AX_Metrics.csv"), ("TEST_Analysis.json", "TPW.AX_Analysis.json"),
                         ("TPW_DCF.json", "TPW.AX_DCF.json"), ("TEST_DashboardSpec.json", "TPW.AX_DashboardSpec.json")):
            shutil.copy(FIX / src, d / "Reports" / dst)
        monkeypatch.setattr(bd, "REPO", d.parent.parent)
        bd.build("TPW.AX")
        assert "slider engine: component base:ok bull:ok bear:ok" in capsys.readouterr().out


class TestBuild:
    def test_build_writes_dashboard_and_reports_iv(self, make_ticker, monkeypatch, capsys):
        d = make_ticker("TEST")
        for n in ("TEST_Metrics.csv", "TEST_Analysis.json", "TEST_DCF.json", "TEST_DashboardSpec.json"):
            shutil.copy(FIX / n, d / "Reports" / n)
        monkeypatch.setattr(bd, "REPO", d.parent.parent)
        out = bd.build("TEST")
        assert out == d / "Reports" / "TEST_Dashboard.html"
        assert out.stat().st_size > 5000
        assert "weighted_iv 3.51" in capsys.readouterr().out

    def test_build_out_override_and_missing_spec(self, make_ticker, monkeypatch, tmp_path):
        d = make_ticker("TEST")
        monkeypatch.setattr(bd, "REPO", d.parent.parent)
        with pytest.raises(FileNotFoundError, match="DashboardSpec"):
            bd.build("TEST")
        for n in ("TEST_Metrics.csv", "TEST_Analysis.json", "TEST_DashboardSpec.json"):
            shutil.copy(FIX / n, d / "Reports" / n)
        out = bd.build("TEST", out=tmp_path / "x.html")
        assert out == tmp_path / "x.html"
        assert "const dcfData = null;" in out.read_text()

    def test_main_usage(self, capsys):
        assert bd.main([]) == 2
        assert "usage" in capsys.readouterr().err


class TestSensitivityAxes:
    """The grid is not always WACC x terminal growth. A multiple-exit model
    (RUA.NZ: probability-weighted EV/Sales scenarios, no Gordon terminal) ships
    `coe_range` x `exit_multiple_range` with the same `matrix` shape, and used
    to render as 'No sensitivity grid in the DCF JSON' -- discarding a complete,
    correct grid because the axis keys were hardcoded."""

    GENERIC: ClassVar[dict] = {
        "axis_note": "Rows: cost of equity (%). Columns: exit EV/Sales multiple.",
        "coe_range": [20, 28, 36],
        "exit_multiple_range": [0.75, 1.5, 2.5],
        "matrix": [[0.0064, 0.0128, 0.0213],
                   [0.0056, 0.0113, 0.0188],
                   [0.0050, 0.0100, 0.0166]],
    }

    def grid_html(self, page, tmp_path):
        js = tmp_path / "dash.js"
        js.write_text(
            bd.node_harness(inline_script(page)).replace(
                "console.log(JSON.stringify(__out));",
                "renderSensitivityMatrix();"
                "console.log(JSON.stringify({h: __el('sensitivityMatrix').innerHTML}));",
            )
        )
        r = subprocess.run(["node", str(js)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout.strip().splitlines()[-1])["h"]

    def test_wacc_terminal_grid_still_renders(self, html, tmp_path):
        """The existing axis pair must keep working -- this is the regression guard."""
        out = self.grid_html(html, tmp_path)
        assert "No sensitivity grid" not in out
        assert "WACC" in out

    def test_generic_axis_grid_renders_instead_of_falling_back(
        self, spec, analysis, csv_text, dcf, tmp_path
    ):
        d = json.loads(json.dumps(dcf))
        d["sensitivity"] = self.GENERIC
        out = self.grid_html(bd.render("TEST", spec, csv_text, analysis, d), tmp_path)
        assert "No sensitivity grid" not in out, "a complete grid was discarded"
        assert "0.75" in out          # column axis from the JSON, not hardcoded
        assert "2.5" in out
        assert "20" in out            # row axis from the JSON
        assert "36" in out

    def test_grid_missing_matrix_still_falls_back(
        self, spec, analysis, csv_text, dcf, tmp_path
    ):
        d = json.loads(json.dumps(dcf))
        d["sensitivity"] = {"coe_range": [20]}
        out = self.grid_html(bd.render("TEST", spec, csv_text, analysis, d), tmp_path)
        assert "No sensitivity grid" in out


class TestValuationPhilosophy:
    """`valuation_philosophy` is a string on some DCF JSONs and an object on
    others (52 of them: AMZN nests summary/approach/wacc_rationale, AFC.NZ
    nests going_concern/uninvestable_conclusion). The template interpolated it
    straight into innerHTML, so every object-shaped one rendered the literal
    text `[object Object]` -- no JS error, just a silently useless panel."""

    def _philosophy_html(self, html, dcf, tmp_path, value):
        d = dict(dcf)
        d["valuation_philosophy"] = value
        page = bd.render("TEST", load("TEST_DashboardSpec.json"), (FIX / "TEST_Metrics.csv").read_text(),
                         load("TEST_Analysis.json"), d)
        js = tmp_path / "dash.js"
        js.write_text(bd._DOM_STUB + inline_script(page) + """
const el = { innerHTML: '', style: {} };
renderPhilosophy(dcfData.valuation_philosophy, el);
console.log(JSON.stringify({ html: el.innerHTML }));
""")
        r = subprocess.run(["node", str(js)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)["html"]

    def test_string_philosophy_renders_verbatim(self, html, dcf, tmp_path):
        out = self._philosophy_html(html, dcf, tmp_path, "Mid-cycle FCF, not the peak year.")
        assert "Mid-cycle FCF, not the peak year." in out
        assert "[object Object]" not in out

    def test_object_philosophy_renders_its_values_not_object_object(self, html, dcf, tmp_path):
        out = self._philosophy_html(html, dcf, tmp_path, {
            "summary": "NTA path times an exit P/B.",
            "wacc_rationale": "12.5% cost of equity, charged once.",
        })
        assert "[object Object]" not in out
        assert "NTA path times an exit P/B." in out
        assert "12.5% cost of equity, charged once." in out

    def test_object_philosophy_labels_each_key(self, html, dcf, tmp_path):
        out = self._philosophy_html(html, dcf, tmp_path, {"going_concern": "Receivership; no forward model."})
        assert "Going concern" in out
        assert "Receivership; no forward model." in out

    def test_nested_list_philosophy_is_flattened(self, html, dcf, tmp_path):
        out = self._philosophy_html(html, dcf, tmp_path, {"key_choices": ["Cost of equity, not WACC.", "No net-debt deduction."]})
        assert "[object Object]" not in out
        assert "Cost of equity, not WACC." in out
        assert "No net-debt deduction." in out

    def test_philosophy_values_are_escaped(self, html, dcf, tmp_path):
        out = self._philosophy_html(html, dcf, tmp_path, {"summary": "Peak <script>alert(1)</script> FCF"})
        assert "<script>alert(1)</script>" not in out
        assert "&lt;script&gt;" in out


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestChartDrawOrder:
    """Chart.js paints datasets by ascending `order`, so a line sharing a chart
    with bars must carry a LOWER order than the bars or the bars cover it --
    the "Revenue & YoY Growth" line was drawn underneath its own bars."""

    def probe(self, html, tmp_path):
        js = tmp_path / "dash.js"
        js.write_text(bd._DOM_STUB + inline_script(html) + r"""
initCharts();
const out = {};
for (const [id, ch] of Object.entries(chartRegistry)) {
    out[id] = ch.data.datasets.map(d => ({ type: d.type, order: d.order,
    borderWidth: d.borderWidth ?? null, pointRadius: d.pointRadius ?? null,
    pointBorderColor: d.pointBorderColor ?? null }));
}
console.log(JSON.stringify(out));
""")
        r = subprocess.run(["node", str(js)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def test_line_draws_above_bars_in_a_mixed_chart(self, html, tmp_path):
        out = self.probe(html, tmp_path)
        rev = out["revenueChart"]
        bar = next(d for d in rev if d["type"] == "bar")
        line = next(d for d in rev if d["type"] == "line")
        assert line["order"] < bar["order"], (
            f"line order {line['order']} must be below bar order {bar['order']}"
        )

    def test_all_bar_chart_keeps_spec_order(self, html, tmp_path):
        """fcfChart is two bars -- their relative order must stay as specified."""
        orders = [d["order"] for d in self.probe(html, tmp_path)["fcfChart"]]
        assert orders == sorted(orders)

    def test_line_over_bars_is_thickened_and_pointed(self, html, tmp_path):
        """Draw order alone is not enough: a hairline over chunky bars still
        reads poorly, so a line sharing a chart with bars gets weight+points."""
        line = next(d for d in self.probe(html, tmp_path)["revenueChart"]
                    if d["type"] == "line")
        assert line["borderWidth"] >= 3
        assert line["pointRadius"] >= 3
        assert line["pointBorderColor"], "points need a halo against the bars"

    def test_standalone_line_keeps_default_weight(self, html, tmp_path):
        """gmChart is a line on its own -- no bars to fight, so leave it alone."""
        line = self.probe(html, tmp_path)["gmChart"][0]
        assert line["borderWidth"] is None
        assert line["pointRadius"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestNonOperatingAssetBridge:
    """A DCF that values only operating cash flows but deducts GROSS net debt
    must credit back the non-operating assets being sold to retire that debt,
    or their value is silently discarded (dcf-methods section 7(d)).

    SKC.NZ ships that bridge as `valuation.{sc}.non_operating_assets` (NZ$91.7m
    base: an unconditional property sale plus investment property at a haircut).
    `sideStreamValue` only knew the `merchant_float_value`/`float_value`/
    `side_stream_value` aliases, so the engine rebuilt an IV short by
    non_operating_assets/shares and failed `validateEngines` -- silently
    demoting every slider on the page to the generic scaler.

    The shipped TEST fixture stores an IV its own fcf-growth engine does not
    reproduce, so each case below first pins the stored IV to the engine's
    actual output (ENGINE_IV) and only then adds a bridge. That isolates the
    bridge as the single variable.
    """

    ENGINE_IV: ClassVar[float] = 2.8224407418865365

    def _engine(self, dcf, tmp_path, sc="base"):
        page = bd.render("TEST", load("TEST_DashboardSpec.json"),
                         (FIX / "TEST_Metrics.csv").read_text(),
                         load("TEST_Analysis.json"), dcf)
        js = tmp_path / "dash.js"
        js.write_text(bd.node_harness(inline_script(page)).replace(
            "console.log(JSON.stringify(__out));",
            f"validateEngines();"
            f"console.log(JSON.stringify(dcfEngineStatus[{sc!r}]));",
        ))
        r = subprocess.run(["node", str(js)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout.strip().splitlines()[-1])

    def _consistent(self, dcf):
        """Pin base to the IV the engine genuinely produces."""
        d = json.loads(json.dumps(dcf))
        d["valuation"]["base"]["intrinsic_value"] = self.ENGINE_IV
        return d

    def _with_bridge(self, dcf, bridge, key="non_operating_assets"):
        d = self._consistent(dcf)
        d["valuation"]["base"][key] = bridge
        d["valuation"]["base"]["intrinsic_value"] += bridge / d["inputs"]["shares_outstanding"]
        return d

    def test_baseline_fixture_validates(self, dcf, tmp_path):
        """Guard the premise: with no bridge, the engine reproduces its IV."""
        st = self._engine(self._consistent(dcf), tmp_path)
        assert st["ok"], f"rebuilt {st['iv']} vs stored {st['target']}"

    def test_engine_credits_non_operating_assets(self, dcf, tmp_path):
        """The bridge is part of equity value: with it credited, the engine
        reproduces the stored IV and the sliders stay live."""
        st = self._engine(self._with_bridge(dcf, 91.7), tmp_path)
        assert st["ok"], (
            f"engine fell back: rebuilt {st['iv']} vs stored {st['target']} "
            "-- non_operating_assets was dropped from the bridge"
        )

    def test_existing_side_stream_alias_still_works(self, dcf, tmp_path):
        """Regression guard: the pre-existing alias must keep validating."""
        st = self._engine(self._with_bridge(dcf, 91.7, key="side_stream_value"), tmp_path)
        assert st["ok"], f"rebuilt {st['iv']} vs stored {st['target']}"

    def test_bridge_actually_moves_the_value(self, dcf, tmp_path):
        """A credited bridge must raise IV by exactly bridge/shares -- proving
        it is added, not merely tolerated by a loose comparison."""
        d = self._consistent(dcf)
        d["valuation"]["base"]["non_operating_assets"] = 91.7
        st = self._engine(d, tmp_path)
        assert st["iv"] == pytest.approx(
            self.ENGINE_IV + 91.7 / d["inputs"]["shares_outstanding"], rel=1e-6
        )
