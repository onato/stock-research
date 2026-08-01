"""Characterization tests for dcf_fields.py, the shared DCF.json extractor.

The fixtures under tests/fixtures/dcf/ are committed copies of real DCF files
(live research/ files are overwritten on every re-research) chosen to cover
every structural variant found in the 69-file corpus, plus synthetics for
branches no committed file exercises.
"""

import dcf_fields as F


class TestNum:
    def test_plain_numbers(self):
        assert F.num(3) == 3.0
        assert F.num(2.5) == 2.5
        assert F.num(-1) == -1.0

    def test_string_forms(self):
        assert F.num("1,234") == 1234.0
        assert F.num("15.0%") == 15.0
        assert F.num("25%") == 25.0
        assert F.num("12.") == 12.0
        assert F.num(" 7 ") == 7.0

    def test_rejects_bool_before_numeric_check(self):
        # bool is an int subclass; True must not become 1.0.
        assert F.num(True) is None
        assert F.num(False) is None

    def test_rejects_non_numeric(self):
        assert F.num(None) is None
        assert F.num("NZD") is None
        assert F.num("") is None
        assert F.num([1, 2, 3]) is None
        assert F.num({"a": 1}) is None


class TestCurrencySuffix:
    def test_simple_suffix(self):
        assert F.currency_suffix("intrinsic_value_per_share_hkd") == "hkd"
        assert F.currency_suffix("weighted_iv_rmb") == "rmb"

    def test_longest_suffix_wins(self):
        # CURRENCIES orders gbp_pence before pence/gbp; WISE.L depends on it.
        assert F.currency_suffix("intrinsic_value_gbp_pence") == "gbp_pence"

    def test_no_suffix(self):
        assert F.currency_suffix("intrinsic_value") == ""
        assert F.currency_suffix("weighted_iv") == ""

    def test_case_insensitive(self):
        assert F.currency_suffix("Intrinsic_Value_USD") == "usd"


class TestLoadDcf:
    def test_missing_file(self, patch_repo):
        assert F.load_dcf("NOPE") is None

    def test_corrupt_json(self, make_ticker):
        d = make_ticker("BAD")
        (d / "Reports" / "BAD_DCF.json").write_text("{ not json")
        assert F.load_dcf("BAD") is None

    def test_valid_file(self, make_ticker):
        d = make_ticker("OK")
        (d / "Reports" / "OK_DCF.json").write_text('{"ticker": "OK"}')
        assert F.load_dcf("OK") == {"ticker": "OK"}


class TestScenarioBlock:
    def test_valuation_key(self, dcf):
        block = F.scenario_block(dcf("FRFHF_DCF"))
        assert set(block) >= {"base", "bull", "bear"}

    def test_scenarios_fallback(self, dcf):
        # PNG.V is 1 of only 2 files using `scenarios` instead of `valuation`.
        block = F.scenario_block(dcf("PNG.V_DCF"))
        assert set(block) >= {"base", "bull", "bear"}

    def test_dict_without_scenarios_rejected(self):
        assert F.scenario_block({"valuation": {"methodology": "x"}}) == {}
        assert F.scenario_block({}) == {}


class TestScenarioIvs:
    def test_dual_currency_both_kept(self, dcf):
        ivs = F.scenario_ivs(dcf("0285.HK_DCF"))
        assert ivs["base"] == {"intrinsic_value_rmb": 19.59,
                               "intrinsic_value_hkd": 21.31}
        assert ivs["bear"]["intrinsic_value_hkd"] == 14.82

    def test_per_share_and_total_both_kept(self, dcf):
        # SRBK carries a whole-equity total AND a per-share IV in one dict.
        # The raw-dicts contract says return both, undifferentiated.
        ivs = F.scenario_ivs(dcf("SRBK_DCF"))
        assert ivs["base"] == {"intrinsic_equity_value": 118.54,
                               "intrinsic_value": 14.46}

    def test_non_numeric_intrinsic_keys_excluded_by_num_only(self):
        # The 'intrinsic' substring filter ADMITS these keys; only num()
        # returning None keeps them out. If num() ever coerces strings like
        # "NZD", currency labels would pollute every ledger row.
        d = {"valuation": {"base": {
            "intrinsic_value": 5.0,
            "intrinsic_value_currency": "NZD",
            "intrinsic_value_note": "per share, fully diluted",
        }}}
        assert F.scenario_ivs(d) == {"base": {"intrinsic_value": 5.0}}

    def test_non_intrinsic_numeric_keys_excluded(self, dcf):
        # 0285.HK scenarios also carry equity_value/net_debt in millions;
        # they lack 'intrinsic' and must not appear.
        ivs = F.scenario_ivs(dcf("0285.HK_DCF"))
        for s in ivs.values():
            assert all("intrinsic" in k for k in s)

    def test_lists_in_scenario_dicts_ignored(self, dcf):
        # PNG.V scenarios hold list-valued assumption paths; num() must
        # return None for them without raising.
        ivs = F.scenario_ivs(dcf("PNG.V_DCF"))
        assert ivs["base"] == {"intrinsic_value_per_share": 2.68}


class TestWeights:
    def test_fractional_passthrough(self, dcf):
        assert F.weights(dcf("SRBK_DCF")) == {"bull": 0.3, "base": 0.45, "bear": 0.25}

    def test_percentage_normalization(self, dcf):
        # No committed file stores percentages; the synthetic fixture is the
        # only coverage of the /100 branch.
        w = F.weights(dcf("synthetic_pct_weights"))
        assert w == {"base": 0.60, "bull": 0.25, "bear": 0.15}
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_sum_outside_99_101_returned_raw(self):
        d = {"probability_weighted": {"weights": {"base": 50, "bull": 30, "bear": 18}}}
        assert F.weights(d) == {"base": 50, "bull": 30, "bear": 18}

    def test_missing_block(self):
        assert F.weights({}) == {}
        assert F.weights({"probability_weighted": {"weights": "n/a"}}) == {}


class TestWeightedIvs:
    def test_currency_suffixed_no_bare_key(self, dcf):
        # 0285.HK declares only suffixed weighted IVs, no bare weighted_iv.
        assert F.weighted_ivs(dcf("0285.HK_DCF")) == {
            "weighted_iv_hkd": 22.75, "weighted_iv_rmb": 20.91}

    def test_weighted_intrinsic_prefix(self, dcf):
        out = F.weighted_ivs(dcf("synthetic_weighted_intrinsic"))
        assert out == {"weighted_intrinsic_value_nzd": 6.0}

    def test_gbp_pence_parallel_series(self, dcf):
        assert F.weighted_ivs(dcf("WISE.L_DCF")) == {
            "weighted_iv": 13.93, "weighted_iv_gbp_pence": 1032.8}


class TestWeightedUpsides:
    def test_upside_substring_match(self, dcf):
        assert F.weighted_upsides(dcf("0285.HK_DCF")) == {"upside_vs_market": 5.0}
        assert F.weighted_upsides(dcf("PNG.V_DCF")) == {
            "weighted_upside_from_current": -52.6}

    def test_no_upside_keys(self, dcf):
        assert F.weighted_upsides(dcf("FRFHF_DCF")) == {}


class TestEntryPrices:
    def test_pure_nested(self, dcf):
        eps = F.entry_prices(dcf("FRFHF_DCF"))
        assert set(eps) == {"base", "bull", "bear"}
        assert eps["base"]["entry_price"] == 1171.97

    def test_mixed_dict_and_scalar(self, dcf):
        # WISE.L: hurdle_rate/methodology scalars alongside scenario dicts.
        # hurdle_rate lacks 'entry' so it must not create a _flat bucket.
        eps = F.entry_prices(dcf("WISE.L_DCF"))
        assert set(eps) == {"base", "bull", "bear"}
        assert eps["base"]["entry_price_gbp_pence"] == 813.2
        # entry_discount_from_current contains 'entry' and survives, even
        # though it is a percentage, not a price - pinned raw-dict behavior.
        assert eps["base"]["entry_discount_from_current"] == -8.2

    def test_flat_shape(self, dcf):
        # PNG.V is the only producer of the _flat bucket.
        assert F.entry_prices(dcf("PNG.V_DCF")) == {"_flat": {
            "base_case_entry": 2.11, "bull_case_entry": 4.11,
            "bear_case_entry": 0.89}}

    def test_partial_scenarios(self, dcf):
        assert set(F.entry_prices(dcf("SRBK_DCF"))) == {"base"}

    def test_bare_scalar_silently_dropped(self, dcf):
        # entry_price: 17.5 (not a dict) -> {} via the isinstance guard.
        # Pinned: the one such file in the wild loses its entry price.
        assert F.entry_prices(dcf("synthetic_scalar_entry")) == {}


class TestHurdleRate:
    def test_unit_inconsistency_preserved(self, dcf):
        # Deliberate pin: hurdle_rate does NOT normalize units. WISE.L stores
        # a fraction, PNG.V a percentage; downstream copes with both.
        assert F.hurdle_rate(dcf("WISE.L_DCF")) == 0.15
        assert F.hurdle_rate(dcf("PNG.V_DCF")) == 15.0

    def test_absent(self, dcf):
        assert F.hurdle_rate(dcf("0285.HK_DCF")) is None
        assert F.hurdle_rate(dcf("SRBK_DCF")) is None
        assert F.hurdle_rate({}) is None


class TestRunIdentity:
    def test_agents_sha_hashes_prompt_files(self, patch_repo):
        agents = patch_repo / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "a.md").write_text("prompt one")
        first = F.agents_sha()
        assert len(first) == 12
        (agents / "a.md").write_text("prompt one, edited")
        assert F.agents_sha() != first

    # Known gap, documented not fixed here: git_head catches OSError but not
    # subprocess.TimeoutExpired, so a hung git would propagate. If that ever
    # bites, add TimeoutExpired to the except clause red-green.
