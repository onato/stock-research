"""scripts/dcf_engine.py -- the owner-FCF component DCF as a script.

The dcf-analyst (Fable, 51% of a run's cost) wrote a bespoke ~48k-char
Python model per ticker to turn its driver paths into a DCF.json and a
workbook. The dashboard already re-runs that model in JS (`runEngine`) and
validates each JSON against it, so the arithmetic was never the agent's
judgment -- only the drivers were. This module is the Python port: it takes
a {TICKER}_Drivers.json and emits the DCF.json the consumers read.

The oracle is APA.AX (2026-09-22), whose JSON the agent verified tied to the
cent against its own recorded assumptions.
"""

import json
from pathlib import Path

import dcf_engine
import pytest

FIX = Path(__file__).parent / "fixtures" / "dcf"


@pytest.fixture
def apa():
    return json.loads((FIX / "APA_Drivers.json").read_text())


@pytest.fixture
def dcf(apa):
    return dcf_engine.build(apa)


class TestProjection:
    def test_component_build_matches_the_agents_year_one(self, dcf):
        p = dcf["projections"]["base"]
        assert p["years"][0] == "FY2027"
        assert p["revenue"][0] == 3096.1
        assert p["adj_ebitda"][0] == 2241.6
        assert p["sbc"][0] == 4.1
        assert p["d_and_a"][0] == 1052.7
        assert p["ebit"][0] == 1184.7
        assert p["cash_tax"][0] == 391.0
        assert p["nopat"][0] == 793.8
        assert p["capex"][0] == 959.8
        assert p["fcf"][0] == 886.7
        assert p["owner_fcf_margin"][0] == 28.6
        assert p["discount_factors"][0] == 0.9346
        assert p["pv_fcf"][0] == 828.7

    def test_final_year_and_horizon(self, dcf):
        p = dcf["projections"]["base"]
        assert len(p["fcf"]) == 10
        assert p["fcf"][-1] == 1603.7
        assert p["years"][-1] == "FY2036"

    def test_short_paths_pad_with_their_last_value(self):
        # The dashboard's asPath() does this; the engine must agree with it.
        proj = dcf_engine.project(
            base_revenue=100.0,
            drivers={"growth_rates": [10, 10, 10], "ebitda_margin_path": [20, 30],
                     "da_pct_path": 5, "capex_pct_path": [5], "cash_tax_rate_path": [25]},
        )
        assert proj["adj_ebitda"] == [22.0, 36.3, 39.9]
        assert proj["d_and_a"] == [5.5, 6.1, 6.7]

    def test_a_loss_year_pays_no_tax(self):
        proj = dcf_engine.project(
            base_revenue=100.0,
            drivers={"growth_rates": [0], "ebitda_margin_path": [5], "da_pct_path": [10],
                     "capex_pct_path": [10], "cash_tax_rate_path": [30]},
        )
        assert proj["ebit"] == [-5.0]
        assert proj["cash_tax"] == [0.0]
        assert proj["nopat"] == [-5.0]

    def test_working_capital_is_on_the_revenue_change(self):
        proj = dcf_engine.project(
            base_revenue=100.0,
            drivers={"growth_rates": [10, 0], "ebitda_margin_path": [0], "wc_capture_pct": 50},
        )
        assert proj["working_capital"] == [5.0, 0.0]

    def test_lease_cost_is_charged_inside_ebit(self):
        proj = dcf_engine.project(
            base_revenue=100.0,
            drivers={"growth_rates": [0], "ebitda_margin_path": [20], "lease_cost_pct_path": [4],
                     "cash_tax_rate_path": [0]},
        )
        assert proj["lease_cost"] == [4.0]
        assert proj["ebit"] == [16.0]


class TestTerminalValue:
    def test_gordon_when_it_is_below_the_cap(self):
        tv = dcf_engine.terminal_value(last_fcf=100.0, rate=10.0, growth=2.0, cap=20.0)
        assert tv.gordon == pytest.approx(1275.0)
        assert tv.value == pytest.approx(1275.0)
        assert tv.bound == "gordon"

    def test_cap_when_gordon_exceeds_it(self):
        tv = dcf_engine.terminal_value(last_fcf=100.0, rate=7.0, growth=2.5, cap=18.0)
        assert tv.value == 1800.0
        assert tv.bound == "cap"

    def test_rate_at_or_below_growth_falls_to_the_cap(self):
        tv = dcf_engine.terminal_value(last_fcf=100.0, rate=2.0, growth=2.5, cap=18.0)
        assert tv.value == 1800.0
        assert tv.bound == "cap"

    def test_negative_terminal_fcf_floors_at_zero(self):
        tv = dcf_engine.terminal_value(last_fcf=-5.0, rate=10.0, growth=2.0, cap=20.0)
        assert tv.value == 0.0
        assert tv.bound == "floor"


class TestValuation:
    def test_scenario_values_match_the_agents(self, dcf):
        v = dcf["valuation"]
        assert (v["bear"]["intrinsic_value"], v["base"]["intrinsic_value"],
                v["bull"]["intrinsic_value"]) == (3.77, 8.0, 9.54)
        assert v["base"]["street_intrinsic_value"] == 8.07
        assert v["base"]["terminal_value"] == 28866.6
        assert v["base"]["terminal_value_bound"] == "cap"
        assert v["base"]["tv_gordon"] == 36528.7
        assert v["base"]["pv_terminal"] == 14674.3
        assert v["base"]["sum_pv_fcf"] == 8626.6
        assert v["base"]["enterprise_value"] == 23301.0
        assert v["base"]["equity_value"] == 10642.0
        assert v["base"]["upside"] == -26.1
        assert v["base"]["terminal_pct_of_value"] == 63.0
        assert v["base"]["implied_exit_multiple"] == 18.0
        assert v["base"]["implied_terminal_ev_to_ebitda"] == 9.6
        assert v["bear"]["terminal_value_bound"] == "gordon"

    def test_entry_prices_rebuild_the_terminal_at_the_hurdle(self, dcf):
        e = dcf["entry_price"]
        assert e["hurdle_rate"] == 0.15
        assert e["base"]["entry_price"] == -2.64
        assert e["base"]["terminal_value_bound"] == "gordon"
        assert e["base"]["pv_interim_fcf_at_hurdle"] == 5899.2
        assert e["base"]["terminal_value_at_hurdle"] == 13150.3
        assert e["base"]["pv_terminal_at_hurdle"] == 3250.6
        assert e["base"]["years_to_terminal"] == 10
        assert e["base"]["entry_discount_from_current"] == -124.4
        assert (e["bear"]["entry_price"], e["bull"]["entry_price"]) == (-3.97, -2.09)
        assert e["weighted_entry_price"] == -2.83

    def test_required_return_table_ties_to_iv_and_entry(self, dcf):
        t = dcf["required_return_table"]
        assert t["scenario"] == "base"
        assert t["returns"] == [7.0, 9, 10, 11, 12, 15]
        assert t["value_per_share"] == [8.0, 4.37, 2.41, 0.92, -0.26, -2.64]

    def test_sensitivity_grid_is_wacc_by_terminal_growth(self, dcf):
        s = dcf["sensitivity"]
        assert s["wacc_range"] == [5.0, 6.0, 7.0, 8.0, 9.0]
        assert s["terminal_growth_range"] == [1.5, 2.0, 2.5, 3.0, 3.5]
        assert s["matrix"][0][0] == 11.02
        assert s["matrix"][2][2] == 8.0            # the model's own point
        assert s["matrix"][3][0] == 5.36
        assert s["terminal_cap_multiple"] == 18

    def test_probability_weighting(self, dcf):
        pw = dcf["probability_weighted"]
        assert pw["weights"] == {"bear": 0.25, "base": 0.5, "bull": 0.25}
        assert pw["weighted_iv"] == 7.33
        assert pw["street_weighted_iv"] == 7.4
        assert pw["weighted_upside_pct"] == -32.3
        assert pw["weights_rationale"].startswith("Bear 25%")

    def test_band_position_collapses_when_sbc_is_immaterial(self, dcf):
        # House and street differ by 1%: an SBC-zero market. The prompt
        # forbids a degenerate 0 here.
        assert dcf["probability_weighted"]["band_position"] is None

    def test_band_position_when_sbc_matters(self, apa):
        for sc in ("base", "bull", "bear"):
            apa["assumptions"][sc]["sbc_pct_path"] = [8.0] * 10
        pw = dcf_engine.build(apa)["probability_weighted"]
        house, street = pw["weighted_iv"], pw["street_weighted_iv"]
        assert street > house * 1.1
        assert pw["band_position"] == pytest.approx((10.83 - house) / (street - house) * 100, abs=0.1)


class TestBridgeAndShares:
    def test_equity_bridge_items_are_added_after_ev(self, apa):
        apa["equity_bridge"] = [{"item": "Surplus land at book", "value": 1330.0}]
        dcf = dcf_engine.build(apa)
        assert dcf["valuation"]["base"]["intrinsic_value"] == 9.0        # +1330/1330 shares
        assert dcf["valuation"]["base"]["equity_bridge_total"] == 1330.0
        # The dashboard engine only knows side_stream_value; emit it so its
        # verify gate still ties.
        assert dcf["valuation"]["base"]["side_stream_value"] == 1330.0
        assert dcf["entry_price"]["base"]["entry_price"] == -1.64

    def test_per_share_uses_the_final_projected_share_count(self, apa):
        apa["inputs"]["projected_shares"] = [1330.0] * 9 + [2660.0]
        dcf = dcf_engine.build(apa)
        assert dcf["valuation"]["base"]["intrinsic_value"] == 4.0

    def test_share_growth_builds_the_projected_path(self, apa):
        del apa["inputs"]["projected_shares"]
        apa["inputs"]["annual_share_growth_pct"] = 1.0
        dcf = dcf_engine.build(apa)
        ps = dcf["inputs"]["projected_shares"]
        assert len(ps) == 10
        assert ps[0] == pytest.approx(1343.3)
        assert ps[-1] == pytest.approx(1330 * 1.01 ** 10, abs=0.1)


class TestCurrency:
    def test_dual_currency_reports_both_and_keeps_the_quote_headline(self, apa):
        apa["inputs"]["quote_currency"] = "NZD"
        apa["inputs"]["fx_rate"] = 1.1        # AUD -> NZD
        apa["current_price"] = 11.9
        dcf = dcf_engine.build(apa)
        v = dcf["valuation"]["base"]
        assert v["intrinsic_value_aud"] == 8.0
        assert v["intrinsic_value"] == 8.8
        assert dcf["probability_weighted"]["weighted_iv_aud"] == 7.33
        assert dcf["probability_weighted"]["weighted_iv"] == pytest.approx(8.06, abs=0.01)
        assert dcf["entry_price"]["base"]["entry_price"] == pytest.approx(-2.64 * 1.1, abs=0.01)

    def test_dual_currency_without_fx_is_refused(self, apa):
        apa["inputs"]["quote_currency"] = "NZD"
        with pytest.raises(dcf_engine.DriversError, match="fx_rate"):
            dcf_engine.build(apa)


class TestContract:
    def test_pass_through_blocks_survive(self, dcf, apa):
        assert dcf["ticker"] == "APA.AX"
        assert dcf["model"] == "owner_fcf_dcf_component"
        assert dcf["inputs"]["last_fcf"] == 403.0
        assert dcf["inputs"]["base_revenue"] == 2977.0
        assert dcf["historical_growth"]["selected_growth_rate"] == 3.5
        assert dcf["scenario_narratives"] == apa["scenario_narratives"]
        assert dcf["valuation_philosophy"] == apa["valuation_philosophy"]
        assert dcf["data_sources"] == apa["data_sources"]
        assert dcf["engine"]["name"] == "dcf_engine"
        assert dcf["assumptions"]["base"]["horizon_years"] == 10

    def test_assumptions_are_echoed_in_the_dashboards_vocabulary(self, dcf):
        a = dcf["assumptions"]["base"]
        for k in ("growth_rates", "ebitda_margin_path", "sbc_pct_path", "da_pct_path",
                  "capex_pct_path", "cash_tax_rate_path", "wc_capture_pct", "wacc",
                  "terminal_growth", "terminal_cap_multiple"):
            assert k in a, k

    def test_reconciliation_gets_its_new_version_filled(self, apa):
        apa["reconciliation"] = {"prior_version": {"valuation_date": "2026-09-21", "weighted": 7.33},
                                 "bridge": [{"item": "Prior weighted IV", "value": 7.33}]}
        rec = dcf_engine.build(apa)["reconciliation"]
        assert rec["new_version"] == {"valuation_date": "2026-09-22", "price_then": 10.83,
                                      "bear": 3.77, "base": 8.0, "bull": 9.54, "weighted": 7.33}
        assert rec["prior_version"]["weighted"] == 7.33

    @pytest.mark.parametrize(("path", "message"), [
        (("inputs", "shares_outstanding"), "inputs.shares_outstanding"),
        (("inputs", "net_debt"), "inputs.net_debt"),
        (("inputs", "base_revenue"), "inputs.base_revenue"),
        (("inputs", "last_fcf"), "inputs.last_fcf"),
        (("assumptions", "bull"), "assumptions.bull"),
        (("assumptions", "base", "wacc"), "assumptions.base.wacc"),
        (("probability_weighted", "weights"), "probability_weighted.weights"),
        (("current_price",), "current_price"),
    ])
    def test_missing_required_fields_are_named(self, apa, path, message):
        node = apa
        for k in path[:-1]:
            node = node[k]
        del node[path[-1]]
        with pytest.raises(dcf_engine.DriversError, match=message):
            dcf_engine.build(apa)

    def test_weights_must_sum_to_one(self, apa):
        apa["probability_weighted"]["weights"] = {"bear": 0.3, "base": 0.5, "bull": 0.3}
        with pytest.raises(dcf_engine.DriversError, match="sum"):
            dcf_engine.build(apa)

    def test_unknown_scenario_keys_are_rejected(self, apa):
        # A typo'd driver silently becomes a no-op in the dashboard too; the
        # engine is the one place that can refuse it.
        apa["assumptions"]["base"]["capex_pct_paht"] = [30.0]
        with pytest.raises(dcf_engine.DriversError, match="capex_pct_paht"):
            dcf_engine.build(apa)


class TestCorpusCheck:
    def test_check_reports_agreement_per_scenario(self, apa):
        dcf = dcf_engine.build(apa)
        report = dcf_engine.check(dcf)
        assert report == {"base": "ok", "bull": "ok", "bear": "ok"}

    def test_check_tolerates_half_a_cent_near_zero(self, apa):
        # VSL.NZ bear: engine -0.012 vs stored -0.01 is rounding, not a model
        # the assumptions fail to describe. Relative tolerance alone flags it.
        dcf = dcf_engine.build(apa)
        dcf["valuation"]["bear"]["intrinsic_value"] = 0.0
        assert dcf_engine._lenient_iv(dcf, "bear") is not None
        dcf["valuation"]["bear"]["intrinsic_value"] = round(dcf_engine._lenient_iv(dcf, "bear") + 0.004, 3)
        assert dcf_engine.check(dcf)["bear"] == "ok"

    def test_check_flags_a_hand_edited_value(self, apa):
        dcf = dcf_engine.build(apa)
        dcf["valuation"]["base"]["intrinsic_value"] = 9.5
        assert dcf_engine.check(dcf)["base"].startswith("mismatch")
