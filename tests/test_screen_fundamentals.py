"""The screener CLI: filters, and the honesty of what it excludes.

A screen that silently drops the tickers it cannot evaluate is worse than no
screen -- it reads as "these are the only candidates" when it means "these are
the ones I could parse". Every exclusion must be visible and explained.
"""

import fundamentals
import pytest
import screen_fundamentals as sf


def fund(ticker, **kw):
    return fundamentals.Fundamentals(ticker=ticker, **kw)


# A HLG.NZ-shaped row that clears every threshold in the target query.
PASSING = {
    "ttm_revenue": 500.0, "ttm_net_income": 46.0, "ttm_fcf": 80.0,
    "ttm_basis": "FY+H1",
    "revenue_growth_5y_total": 0.63, "revenue_cagr_5y": 0.103,
    "revenue_growth_1y": 0.118, "earnings_growth_1y": 0.09,
    "roe": 0.397, "debt_to_equity": 0.0, "peg": 0.72, "derived_eps": 0.776,
}


class TestFilters:
    def test_a_fully_passing_row_survives(self):
        rows = [fund("HLG.NZ", **PASSING)]
        crit = sf.Criteria(min_roe=0.15, max_de=1.0, min_fcf=0.0)
        assert [r.ticker for r in sf.select(rows, crit).passed] == ["HLG.NZ"]

    @pytest.mark.parametrize(
        ("field", "value", "crit"),
        [
            ("roe", 0.10, {"min_roe": 0.15}),
            ("debt_to_equity", 1.5, {"max_de": 1.0}),
            ("ttm_fcf", -5.0, {"min_fcf": 0.0}),
            ("revenue_growth_1y", 0.01, {"min_revenue_growth_1y": 0.05}),
            ("earnings_growth_1y", 0.01, {"min_earnings_growth_1y": 0.05}),
            ("peg", 1.4, {"max_peg": 1.0}),
            ("revenue_growth_5y_total", 0.2, {"min_revenue_growth_5y_total": 0.5}),
        ],
    )
    def test_each_threshold_excludes(self, field, value, crit):
        rows = [fund("T", **{**PASSING, field: value})]
        result = sf.select(rows, sf.Criteria(**crit))
        assert result.passed == []
        assert [r.ticker for r in result.failed] == ["T"]

    def test_zero_debt_passes_a_min_de_of_zero(self):
        """HLG.NZ genuinely has no debt; `>= 0` must include it."""
        rows = [fund("HLG.NZ", **{**PASSING, "debt_to_equity": 0.0})]
        crit = sf.Criteria(min_de=0.0, max_de=1.0)
        assert [r.ticker for r in sf.select(rows, crit).passed] == ["HLG.NZ"]

    def test_a_missing_value_is_unscreenable_not_a_pass(self):
        """None must never satisfy a threshold by accident."""
        rows = [fund("AGL.NZ", **{**PASSING, "roe": None})]
        result = sf.select(rows, sf.Criteria(min_roe=0.15))
        assert result.passed == []
        assert result.unscreenable[0].ticker == "AGL.NZ"

    def test_no_criteria_passes_everything_screenable(self):
        rows = [fund("A", **PASSING), fund("B", **PASSING)]
        assert len(sf.select(rows, sf.Criteria()).passed) == 2


class TestUnscreenable:
    def test_rows_with_no_data_are_reported_with_reasons(self):
        rows = [fund("FRFHF", reasons=("no-core-metrics",))]
        result = sf.select(rows, sf.Criteria(min_roe=0.15))
        assert result.passed == []
        assert result.unscreenable[0].reasons == ("no-core-metrics",)

    def test_unscreenable_is_never_silently_dropped(self):
        rows = [fund("HLG.NZ", **PASSING), fund("IFT.NZ", reasons=("no-ttm:revenue",))]
        result = sf.select(rows, sf.Criteria(min_roe=0.15))
        assert len(result.passed) + len(result.unscreenable) == 2

    def test_a_row_with_no_reasons_names_the_missing_fields(self):
        """`missing a screened field` does not tell you which one."""
        rows = [fund("ARG.NZ", **{**PASSING, "peg": None, "roe": None})]
        result = sf.select(rows, sf.Criteria(min_roe=0.15, max_peg=1.0))
        assert result.unscreenable[0].reasons == ()
        line = sf.unscreenable_line(result.unscreenable[0],
                                    sf.Criteria(min_roe=0.15, max_peg=1.0))
        assert "roe" in line
        assert "peg" in line

    def test_only_criteria_fields_can_make_a_row_unscreenable(self):
        """A missing PEG must not exclude a screen that never asked for PEG."""
        rows = [fund("ANZ.NZ", **{**PASSING, "peg": None})]
        assert sf.select(rows, sf.Criteria(min_roe=0.15)).passed


class TestFyBasis:
    def test_fy_basis_is_excluded_by_default(self):
        rows = [fund("TAH.NZ", **{**PASSING, "ttm_basis": "FY"})]
        result = sf.select(rows, sf.Criteria(min_roe=0.15))
        assert result.passed == []
        assert [r.ticker for r in result.fy_basis] == ["TAH.NZ"]

    def test_opt_in_includes_them(self):
        rows = [fund("TAH.NZ", **{**PASSING, "ttm_basis": "FY"})]
        crit = sf.Criteria(min_roe=0.15, allow_fy_basis=True)
        assert [r.ticker for r in sf.select(rows, crit).passed] == ["TAH.NZ"]

    def test_an_fy_basis_row_that_fails_a_threshold_is_failed_not_fy_listed(self):
        rows = [fund("T", **{**PASSING, "ttm_basis": "FY", "roe": 0.01})]
        result = sf.select(rows, sf.Criteria(min_roe=0.15))
        assert result.fy_basis == []
        assert result.failed[0].ticker == "T"


class TestExchangeFilter:
    def test_exchange_maps_to_a_suffix(self):
        assert sf.suffix_for(exchange="NZX", suffix=None) == ".NZ"
        assert sf.suffix_for(exchange="LSE", suffix=None) == ".L"

    def test_raw_suffix_passes_through(self):
        assert sf.suffix_for(exchange=None, suffix=".NZ") == ".NZ"

    def test_a_bare_suffix_is_normalised(self):
        assert sf.suffix_for(exchange=None, suffix="NZ") == ".NZ"

    def test_conflicting_exchange_and_suffix_is_an_error(self):
        with pytest.raises(ValueError, match="disagree"):
            sf.suffix_for(exchange="NZX", suffix=".L")

    def test_agreeing_exchange_and_suffix_is_fine(self):
        assert sf.suffix_for(exchange="NZX", suffix=".NZ") == ".NZ"

    def test_unknown_exchange_is_an_error(self):
        with pytest.raises(ValueError, match="unknown exchange"):
            sf.suffix_for(exchange="NASDAQ", suffix=None)


class TestFiveYearAmbiguity:
    """`> 0.5` almost certainly means total growth, not a 7.6x CAGR."""

    def test_a_large_cagr_threshold_warns(self):
        assert sf.cagr_note(0.5) is not None
        assert "7.6" in sf.cagr_note(0.5)

    def test_a_plausible_cagr_threshold_is_silent(self):
        assert sf.cagr_note(0.10) is None

    def test_no_threshold_is_silent(self):
        assert sf.cagr_note(None) is None


class TestCli:
    def test_runs_end_to_end_and_reports_all_three_blocks(self, capsys):
        rows = [
            fund("HLG.NZ", **PASSING),
            fund("TAH.NZ", **{**PASSING, "ttm_basis": "FY"}),
            fund("IFT.NZ", reasons=("no-ttm:revenue",)),
            fund("AIA.NZ", **{**PASSING, "roe": 0.04}),
        ]
        sf.report(sf.select(rows, sf.Criteria(min_roe=0.15)), sf.Criteria(min_roe=0.15))
        out = capsys.readouterr().out
        assert "HLG.NZ" in out
        assert "TAH.NZ" in out
        assert "FY-BASIS" in out
        assert "IFT.NZ" in out
        assert "no-ttm:revenue" in out
        assert "UNSCREENABLE" in out

    def test_reports_the_no_fx_caveat(self, capsys):
        sf.report(sf.select([fund("A", **PASSING)], sf.Criteria()), sf.Criteria())
        assert "FX" in capsys.readouterr().out

    def test_labels_peg_as_dcf_derived(self, capsys):
        """PEG is not an analyst estimate and must not read as one."""
        sf.report(sf.select([fund("A", **PASSING)], sf.Criteria(max_peg=1.0)),
                  sf.Criteria(max_peg=1.0))
        assert "DCF" in capsys.readouterr().out


class TestTargetQuery:
    """The seven-criterion NZX screen this work exists to answer."""

    def test_reproduces_the_users_query(self):
        rows = [
            fund("HLG.NZ", **PASSING),
            fund("SEK.NZ", **{**PASSING, "roe": 0.098}),          # ROE too low
            fund("ATM.NZ", **{**PASSING, "peg": 5.55}),           # PEG too high
            fund("AIR.NZ", **{**PASSING, "ttm_fcf": -447.0}),     # negative FCF
            fund("SUM.NZ", reasons=("no-ttm:net_income",)),       # no data
        ]
        crit = sf.Criteria(
            min_revenue_growth_5y_total=0.5, min_revenue_growth_1y=0.05,
            min_earnings_growth_1y=0.05, min_roe=0.15,
            min_de=0.0, max_de=1.0, min_fcf=0.0, min_peg=0.0, max_peg=1.0)
        result = sf.select(rows, crit)
        assert [r.ticker for r in result.passed] == ["HLG.NZ"]
        assert {r.ticker for r in result.failed} == {"SEK.NZ", "ATM.NZ", "AIR.NZ"}
        assert [r.ticker for r in result.unscreenable] == ["SUM.NZ"]
