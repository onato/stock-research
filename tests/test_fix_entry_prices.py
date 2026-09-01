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

    def test_refuses_a_growing_terminal_share_count(self):
        """DUOL dilutes 49.0m -> 60.64m and divides the terminal value by the
        grown count. Recomputing on a flat count silently changes the model."""
        for where in ("projected", "stated"):
            d = _dcf()
            if where == "projected":
                for s in ("bear", "base", "bull"):
                    d["projections"][s]["shares"] = [1196.6] * 9 + [1400.0]
            else:
                d["entry_price"]["base"]["terminal_year_shares"] = 1400.0
            with pytest.raises(X.NotRecomputableError):
                X.fix(d)
