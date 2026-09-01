"""Recompute hurdle entry prices in place, without re-running the model.

The 2026-09-01 spec fix (see run_evals.check_entry_price_hurdle) left 42
committed DCFs carrying entry prices whose terminal value was built at WACC.
Nothing about the underlying model is wrong -- only the entry-price arithmetic
-- so these are repaired by recomputation from the JSON, never by a re-research.
"""
import json

import fix_entry_prices as X
import pytest


def _dcf(entry=59.17, **kw):
    fcf = kw.get("fcf", [7876.0, 8554.0, 9209.0, 9806.0, 10385.0,
                         10446.0, 9993.0, 9617.0, 9465.0, 9506.0])
    scen = {"wacc": 7.5, "terminal_growth": 1.5, "terminal_cap_multiple": 15}
    return {
        "current_price": 76.81,
        "inputs": {"shares_outstanding": 1196.6, "net_debt": 11045.0},
        "assumptions": {s: dict(scen) for s in ("bear", "base", "bull")},
        "projections": {s: {"fcf": list(fcf)} for s in ("bear", "base", "bull")},
        "entry_price": {"hurdle_rate": 0.15,
                        "base": {"entry_price": entry},
                        "bear": {"entry_price": 35.81},
                        "bull": {"entry_price": 71.13}},
        "probability_weighted": {"weights": {"bear": .25, "base": .5, "bull": .25}},
    }


class TestRecompute:
    def test_corrects_the_wacc_built_entry_price(self):
        d = _dcf()
        assert X.fix(d)["base"]["entry_price"] == pytest.approx(44.48, abs=.01)

    def test_reweights_the_weighted_entry_price(self):
        d = _dcf()
        X.fix(d)
        w = d["entry_price"]["weighted_entry_price"]
        exp = .25 * d["entry_price"]["bear"]["entry_price"] \
            + .50 * d["entry_price"]["base"]["entry_price"] \
            + .25 * d["entry_price"]["bull"]["entry_price"]
        assert w == pytest.approx(exp, abs=.01)

    def test_is_idempotent(self):
        d = _dcf()
        X.fix(d)
        first = json.dumps(d, sort_keys=True)
        X.fix(d)
        assert json.dumps(d, sort_keys=True) == first

    def test_records_the_superseded_value_once(self):
        d = _dcf()
        X.fix(d)
        was = d["entry_price"]["base"]["entry_price_superseded"]
        X.fix(d)
        assert d["entry_price"]["base"]["entry_price_superseded"] == was

    def test_rebuilds_the_required_return_table_per_row(self):
        d = _dcf()
        d["required_return_table"] = {"returns": [7.5, 15],
                                      "value_per_share": [102.45, 59.17]}
        X.fix(d)
        vps = d["required_return_table"]["value_per_share"]
        assert vps[1] == pytest.approx(d["entry_price"]["base"]["entry_price"],
                                       abs=.01)

    def test_refuses_a_non_fcf_model(self):
        """SUM.NZ discounts Underlying Profit -- must not be touched."""
        d = _dcf()
        d["projections"] = {s: {} for s in ("bear", "base", "bull")}
        with pytest.raises(X.NotRecomputableError):
            X.fix(d)

    def test_refuses_when_the_interim_leg_does_not_tie(self):
        """If our FCF reconstruction disagrees with the file's own stated
        PV of interim flows, the file means something we do not model."""
        d = _dcf()
        d["entry_price"]["base"]["pv_interim_fcf_at_hurdle"] = 999.0
        with pytest.raises(X.NotRecomputableError):
            X.fix(d)

    def test_honours_years_to_terminal(self):
        d = _dcf()
        for s in ("bear", "base", "bull"):
            d["entry_price"][s]["years_to_terminal"] = 5
        X.fix(d)
        fcf = d["projections"]["base"]["fcf"][:5]
        pv = sum(f / 1.15 ** (i + 1) for i, f in enumerate(fcf))
        tv = min(fcf[-1] * 1.015 / (.15 - .015), fcf[-1] * 15)
        exp = (pv + tv / 1.15 ** 5 - 11045.0) / 1196.6
        assert d["entry_price"]["base"]["entry_price"] == pytest.approx(exp, abs=.01)

    def test_zero_terminal_fcf_does_not_crash(self):
        """A terminal-year FCF of 0 makes the exit multiple undefined."""
        d = _dcf(fcf=[10.0, 8.0, 5.0, 2.0, 0.0])
        X.fix(d)
        assert "terminal_multiple_at_hurdle" not in d["entry_price"]["base"]
        assert d["entry_price"]["base"]["entry_price"] is not None


class TestRefusals:
    """Guards against corrupting a file that means something different."""

    def test_refuses_a_relabelled_fcf_series(self):
        """SUM.NZ's `fcf` rows are Underlying Profit, flagged by `fcf_label`;
        RYM.NZ's are dividends, flagged by `fcf_note`."""
        for marker in ("fcf_label", "fcf_note"):
            d = _dcf()
            for s in ("bear", "base", "bull"):
                d["projections"][s][marker] = "Underlying Profit"
            with pytest.raises(X.NotRecomputableError):
                X.fix(d)

    def test_refuses_currency_suffixed_interim_pv(self):
        """KKS.F states pv_interim_distributions_at_hurdle_kzt_m against an
        entry price in EUR -- the legs are in different currencies."""
        d = _dcf()
        d["entry_price"]["base"]["pv_interim_distributions_at_hurdle_kzt_m"] = 2283920.0
        with pytest.raises(X.NotRecomputableError):
            X.fix(d)

    def test_leaves_an_already_correct_file_untouched(self):
        """DUOL already passes the checker; recomputation must be a no-op."""
        d = _dcf(entry=44.48)
        before = json.dumps(d, sort_keys=True)
        X.fix(d)
        assert "entry_price_superseded" not in d["entry_price"]["base"]
        assert json.loads(before)["entry_price"]["base"]["entry_price"] == \
            pytest.approx(d["entry_price"]["base"]["entry_price"], abs=.01)

    def test_refuses_dilution_with_no_stated_divisor(self):
        """A `shares` array that grows with no terminal/exit count named is
        ambiguous: we must not guess which divisor the model used."""
        d = _dcf()
        for s in ("bear", "base", "bull"):
            d["projections"][s]["shares"] = [1196.6] * 9 + [1400.0]
        with pytest.raises(X.NotRecomputableError):
            X.fix(d)

    def test_honours_a_stated_exit_share_count(self):
        """DUOL names its divisor `exit_shares` rather than
        `terminal_year_shares`; both must be recognised."""
        for key in ("terminal_year_shares", "exit_shares"):
            d = _dcf()
            d["entry_price"]["base"][key] = 1400.0
            X.fix(d)
            fcf = d["projections"]["base"]["fcf"]
            pv = sum(f / 1.15 ** (i + 1) for i, f in enumerate(fcf))
            tv = min(fcf[-1] * 1.015 / (0.15 - 0.015), fcf[-1] * 15)
            exp = (pv + tv / 1.15 ** 10 - 11045.0) / 1400.0
            assert d["entry_price"]["base"]["entry_price"] == \
                pytest.approx(exp, abs=0.01)


class TestDilutedAndBridged:
    """Files that divide by a TERMINAL-year share count and add non-operating
    assets to the bridge (CCC.NZ, ENS.NZ, GNE.NZ).

    These were refused wholesale until 2026-09-01: recomputing them on a flat
    share count and a bare net-debt bridge silently restates the model. The fix
    is to honour their convention, not to bypass the guard -- so the guard now
    fires only when the file gives no explicit terminal share count to use.
    """

    @staticmethod
    def _dcf(entry, *, term_shares=1400.0, equity_investments=None):
        fcf = [10.0, 12.0, 14.0, 16.0, 18.0]
        blk = {"entry_price": entry, "years_to_terminal": 5,
               "net_debt": 50.0, "terminal_year_shares": term_shares}
        if equity_investments is not None:
            blk["equity_investments"] = equity_investments
        scen = {"wacc": 9.0, "terminal_growth": 2.0, "terminal_cap_multiple": 12}
        return {
            "current_price": 1.0,
            "inputs": {"shares_outstanding": 1000.0, "net_debt": 50.0},
            "assumptions": {s: dict(scen) for s in ("bear", "base", "bull")},
            "projections": {s: {"owner_fcf": list(fcf)}
                            for s in ("bear", "base", "bull")},
            "entry_price": {"hurdle_rate": 0.15, "base": dict(blk)},
        }

    def _expected(self, d, *, ei=0.0):
        f = d["projections"]["base"]["owner_fcf"]
        pv = sum(x / 1.15 ** (i + 1) for i, x in enumerate(f))
        tv = min(f[-1] * 1.02 / (0.15 - 0.02), f[-1] * 12)
        return (pv + tv / 1.15 ** 5 - 50.0 + ei) / 1400.0

    def test_divides_by_the_terminal_year_share_count(self):
        d = self._dcf(0.0)
        X.fix(d)
        assert d["entry_price"]["base"]["entry_price"] == \
            pytest.approx(self._expected(d), abs=1e-4)

    def test_keeps_equity_investments_in_the_bridge(self):
        d = self._dcf(0.0, equity_investments=1.199)
        X.fix(d)
        assert d["entry_price"]["base"]["entry_price"] == \
            pytest.approx(self._expected(d, ei=1.199), abs=1e-4)

    def test_uses_owner_fcf_when_fcf_is_absent(self):
        d = self._dcf(0.0)
        assert "fcf" not in d["projections"]["base"]
        X.fix(d)
        assert d["entry_price"]["base"]["pv_interim_fcf_at_hurdle"] is not None

    def test_still_refuses_dilution_with_no_terminal_count_given(self):
        """A growing `shares` array with no terminal_year_shares is still
        ambiguous -- we must not guess which divisor the model used."""
        d = self._dcf(0.0, term_shares=None)
        del d["entry_price"]["base"]["terminal_year_shares"]
        d["projections"]["base"]["shares"] = [1000.0] * 4 + [1400.0]
        with pytest.raises(X.NotRecomputableError):
            X.fix(d)

    def test_infers_the_divisor_the_file_demonstrably_used(self):
        """ENS.NZ names no terminal_year_shares, but states both
        entry_equity_value and entry_price -- their ratio proves the divisor,
        and it matches the last projected share count. Inferring from the
        file's own arithmetic is evidence, not a guess."""
        d = self._dcf(0.0, term_shares=None)
        del d["entry_price"]["base"]["terminal_year_shares"]
        d["projections"]["base"]["shares"] = [1000.0] * 4 + [1400.0]
        d["entry_price"]["base"]["entry_equity_value"] = 1400.0
        d["entry_price"]["base"]["entry_price"] = 1.0   # => divisor 1400
        X.fix(d)
        assert d["entry_price"]["base"]["entry_price"] == \
            pytest.approx(self._expected(d), abs=1e-4)

    def test_does_not_infer_when_the_ratio_contradicts_the_projection(self):
        """If the implied divisor does not match the projected share path,
        the file means something else -- refuse rather than trust the ratio."""
        d = self._dcf(0.0, term_shares=None)
        del d["entry_price"]["base"]["terminal_year_shares"]
        d["projections"]["base"]["shares"] = [1000.0] * 4 + [1400.0]
        d["entry_price"]["base"]["entry_equity_value"] = 900.0
        d["entry_price"]["base"]["entry_price"] = 1.0   # => divisor 900, not 1400
        with pytest.raises(X.NotRecomputableError):
            X.fix(d)


class TestNonFCFEngines:
    """Models that capitalize a non-FCF flow (SUM.NZ: Underlying Profit at a
    cost of equity with an exit-P/E cap).

    The build-TV-at-the-hurdle rule is about the RATE, not the flow: SUM.NZ
    built its terminal value at the 11% cost of equity and then discounted it
    at 15%, which is the same defect as the FCF case. But the flow is not
    owner FCF, so the generic path must stay refused -- only an explicit
    opt-in that names the engine may recompute it.
    """

    @staticmethod
    def _sum_like(entry=11.06):
        up = [222.3, 237.9, 253.2, 271.2, 289.8]
        return {
            "current_price": 8.54,
            "inputs": {"shares_outstanding": 243.483, "net_debt": 0},
            "assumptions": {"base": {"wacc": 11.0, "terminal_growth": 3.0,
                                     "exit_pe_cap": 14.9}},
            "projections": {"base": {"fcf": list(up),
                                     "fcf_label": "Underlying Profit"}},
            "entry_price": {"hurdle_rate": 0.15,
                            "base": {"entry_price": entry,
                                     "years_to_terminal": 5,
                                     "pv_interim_at_hurdle": 838.8}},
        }

    def test_generic_path_still_refuses_a_relabelled_series(self):
        with pytest.raises(X.NotRecomputableError):
            X.fix(self._sum_like())

    def test_capitalized_engine_rebuilds_at_the_hurdle(self):
        d = self._sum_like()
        X.fix_capitalized(d)
        up = d["projections"]["base"]["fcf"]
        pv = sum(x / 1.15 ** (i + 1) for i, x in enumerate(up))
        tv = min(up[-1] * 1.03 / (0.15 - 0.03), up[-1] * 14.9)
        exp = (pv + tv / 1.15 ** 5) / 243.483
        assert d["entry_price"]["base"]["entry_price"] == pytest.approx(exp, abs=.01)

    def test_capitalized_engine_never_subtracts_net_debt(self):
        """Underlying Profit is already post-finance-cost -- deducting net
        debt would double-count it (inputs.notes says so explicitly)."""
        d = self._sum_like()
        d["inputs"]["net_debt"] = 2000.0
        X.fix_capitalized(d)
        no_debt = self._sum_like()
        X.fix_capitalized(no_debt)
        assert d["entry_price"]["base"]["entry_price"] == \
            pytest.approx(no_debt["entry_price"]["base"]["entry_price"], abs=1e-6)

    def test_capitalized_engine_records_the_superseded_value(self):
        d = self._sum_like()
        X.fix_capitalized(d)
        assert d["entry_price"]["base"]["entry_price_superseded"] == 11.06
