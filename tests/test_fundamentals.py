"""Per-ticker derived fundamentals: TTM, growth, ratios, PEG.

Every number here is a candidate for a plausible-wrong answer, so the tests
are built from real corpus shapes rather than round synthetic figures:

  * EBO.NZ pins the FY+H1 TTM reconstruction (NZ filers report half-yearly,
    so a TTM is never four quarters).
  * WISE.L pins two traps at once -- EPS stored in cents, and a price quoted
    in GBP pence against USD financials.
"""

import fundamentals
import pytest

MILLIONS = "millions"


def rows(*specs):
    """Build metrics_normalized-shaped rows: (period, {field: value})."""
    out = []
    for period, vals in specs:
        row = {"period": period, "currency": "NZD", **vals}
        out.append(row)
    return out


class TestTtmFyPlusH1:
    """The dominant NZ path: FY(Y-1) + H1(Y) - H1(Y-1).

    Verified against EBO.NZ's real figures.
    """

    def test_ebo_nz_reconstruction(self):
        r = rows(
            ("FY2025", {"revenue": 12266.9}),
            ("H1 FY2025", {"revenue": 5991.4}),
            ("H1 FY2026", {"revenue": 6767.7}),
        )
        value, basis = fundamentals.ttm(r, "revenue")
        assert basis == "FY+H1"
        assert value == pytest.approx(13043.2)

    def test_requires_the_prior_year_half(self):
        """Without H1(Y-1) the difference is not a trailing twelve months."""
        r = rows(
            ("FY2025", {"revenue": 12266.9}),
            ("H1 FY2026", {"revenue": 6767.7}),
        )
        value, basis = fundamentals.ttm(r, "revenue")
        assert basis == "FY"          # falls back, does not fabricate
        assert value == pytest.approx(12266.9)

    def test_hyphen_and_space_spellings_reconcile(self):
        """`FY2025` + `H1-2026` - `H1-2025` must work like the FY spellings."""
        r = rows(
            ("FY2025", {"revenue": 470.7}),
            ("H1-2025", {"revenue": 240.0}),
            ("H1-2026", {"revenue": 275.2}),
        )
        value, basis = fundamentals.ttm(r, "revenue")
        assert basis == "FY+H1"
        assert value == pytest.approx(505.9)      # HLG.NZ, hand-verified


class TestTtmQuarters:
    def test_four_quarters_sum(self):
        r = rows(
            ("Q1 2025", {"revenue": 10.0}), ("Q2 2025", {"revenue": 11.0}),
            ("Q3 2025", {"revenue": 12.0}), ("Q4 2025", {"revenue": 13.0}),
        )
        value, basis = fundamentals.ttm(r, "revenue")
        assert basis == "4Q"
        assert value == pytest.approx(46.0)

    def test_trailing_four_across_the_year_boundary(self):
        r = rows(
            ("Q4 2024", {"revenue": 9.0}), ("Q1 2025", {"revenue": 10.0}),
            ("Q2 2025", {"revenue": 11.0}), ("Q3 2025", {"revenue": 12.0}),
        )
        value, basis = fundamentals.ttm(r, "revenue")
        assert basis == "4Q"
        assert value == pytest.approx(42.0)

    def test_a_gap_rejects_the_quarter_path(self):
        """Three quarters plus a hole is not a year."""
        r = rows(
            ("Q1 2025", {"revenue": 10.0}), ("Q2 2025", {"revenue": 11.0}),
            ("Q4 2025", {"revenue": 13.0}), ("FY2024", {"revenue": 40.0}),
        )
        value, basis = fundamentals.ttm(r, "revenue")
        assert basis == "FY"
        assert value == pytest.approx(40.0)


class TestTtmFallbackAndRefusal:
    def test_latest_full_year(self):
        r = rows(("FY2024", {"revenue": 100.0}), ("FY2025", {"revenue": 110.0}))
        assert fundamentals.ttm(r, "revenue") == (110.0, "FY")

    def test_nothing_usable_yields_none(self):
        assert fundamentals.ttm(rows(), "revenue") == (None, "NONE")

    def test_null_values_are_not_zero(self):
        """A missing FCF must not read as zero FCF."""
        r = rows(("FY2025", {"revenue": 100.0, "free_cash_flow": None}))
        assert fundamentals.ttm(r, "free_cash_flow") == (None, "NONE")

    def test_irregular_periods_are_never_summed(self):
        """A 15-month year is not a comparable annual figure."""
        r = rows(("FY2017-15mo", {"revenue": 125.0}))
        assert fundamentals.ttm(r, "revenue") == (None, "NONE")


class TestGrowth:
    def test_one_year_growth_compares_like_with_like(self):
        """TTM vs prior TTM -- never a TTM numerator over an FY denominator."""
        r = rows(
            ("FY2024", {"revenue": 400.0}),
            ("FY2025", {"revenue": 470.7}),
            ("H1-2024", {"revenue": 200.0}),
            ("H1-2025", {"revenue": 240.0}),
            ("H1-2026", {"revenue": 275.2}),
        )
        f = fundamentals.compute("HLG.NZ", r, dcf=None)
        # ttm = 505.9 ; prior ttm = 400.0 + 240.0 - 200.0 = 440.0
        assert f.revenue_growth_1y == pytest.approx(505.9 / 440.0 - 1)

    def test_five_year_cagr_and_total_are_both_reported(self):
        r = rows(("FY2020", {"revenue": 100.0}), ("FY2025", {"revenue": 200.0}))
        f = fundamentals.compute("T", r, dcf=None)
        assert f.revenue_growth_5y_total == pytest.approx(1.0)
        assert f.revenue_cagr_5y == pytest.approx(2 ** 0.2 - 1)

    def test_nonpositive_base_refuses_rather_than_returning_a_complex_root(self):
        r = rows(("FY2020", {"net_income": -5.0}), ("FY2025", {"net_income": 20.0}))
        f = fundamentals.compute("AGL.NZ", r, dcf=None)
        assert f.earnings_cagr_5y is None
        assert "cagr-nonpositive-base:net_income" in f.reasons

    def test_irregular_year_cannot_anchor_a_cagr(self):
        r = rows(("FY2017-15mo", {"revenue": 100.0}),
                 ("FY2022", {"revenue": 200.0}))
        f = fundamentals.compute("T", r, dcf=None)
        assert f.revenue_cagr_5y is None


class TestRatios:
    def test_roe_uses_average_equity(self):
        """NZ equity jumps on capital raises; point-in-time understates ROE.

        Equity 100 -> 200 with TTM net income 30 is 30/150 = 0.20, not
        30/200 = 0.15 nor 30/100 = 0.30.
        """
        r = rows(
            ("FY2024", {"net_income": 25.0, "shareholders_equity": 100.0}),
            ("FY2025", {"net_income": 30.0, "shareholders_equity": 200.0}),
        )
        f = fundamentals.compute("AGL.NZ", r, dcf=None)
        assert f.roe == pytest.approx(0.20)

    def test_roe_with_a_single_equity_observation_is_flagged(self):
        r = rows(("FY2025", {"net_income": 30.0, "shareholders_equity": 200.0}))
        f = fundamentals.compute("T", r, dcf=None)
        assert f.roe == pytest.approx(0.15)
        assert "roe-point-equity" in f.reasons

    def test_debt_to_equity_at_the_latest_period(self):
        r = rows(("FY2025", {"total_debt": 12.565, "shareholders_equity": 24.637}))
        f = fundamentals.compute("ALF.NZ", r, dcf=None)
        assert f.debt_to_equity == pytest.approx(12.565 / 24.637)

    def test_zero_debt_is_zero_not_missing(self):
        """HLG.NZ genuinely carries no debt -- that must pass a `>= 0` filter."""
        r = rows(("FY2025", {"total_debt": 0.0, "shareholders_equity": 111.9}))
        assert fundamentals.compute("HLG.NZ", r, dcf=None).debt_to_equity == 0.0

    def test_negative_equity_refuses(self):
        r = rows(("FY2014", {"total_debt": 7.109, "shareholders_equity": -4.094}))
        f = fundamentals.compute("ALF.NZ", r, dcf=None)
        assert f.debt_to_equity is None
        assert "negative-equity" in f.reasons


class TestDerivedEps:
    """The stored `eps` column is unusable cross-ticker.

    13 tickers store cents and 5 have a shares-scale bug, and
    metrics_normalized deliberately does not scale per-share figures. EPS is
    therefore always derived from two normalised money/count columns.
    """

    def test_ignores_a_cents_valued_stored_eps(self):
        r = rows(("FY2026", {"net_income": 498.7, "shares_outstanding": 1029.7,
                             "eps": 48.43}))
        f = fundamentals.compute("WISE.L", r, dcf=None)
        assert f.derived_eps == pytest.approx(0.4843, rel=1e-3)

    def test_missing_shares_yields_no_eps(self):
        r = rows(("FY2025", {"net_income": 100.0, "shares_outstanding": None}))
        f = fundamentals.compute("T", r, dcf=None)
        assert f.derived_eps is None


class TestPeg:
    """PEG uses the DCF's selected growth rate, which is stored as a PERCENT."""

    def test_percent_semantics(self):
        r = rows(("FY2025", {"net_income": 100.0, "shares_outstanding": 100.0,
                             "currency": "NZD"}))
        dcf = {"current_price": 10.0, "currency": "NZD",
               "historical_growth": {"selected_growth_rate": 20.0}}
        f = fundamentals.compute("T", r, dcf=dcf)
        assert f.derived_eps == pytest.approx(1.0)
        assert f.peg == pytest.approx(0.5)          # PE 10 / growth 20

    def test_nonpositive_growth_refuses(self):
        r = rows(("FY2025", {"net_income": 100.0, "shares_outstanding": 100.0}))
        dcf = {"current_price": 10.0, "currency": "NZD",
               "historical_growth": {"selected_growth_rate": -0.4}}
        f = fundamentals.compute("SPK.NZ", r, dcf=dcf)
        assert f.peg is None
        assert "peg-nonpositive-growth" in f.reasons

    def test_missing_growth_is_reported(self):
        r = rows(("FY2025", {"net_income": 100.0, "shares_outstanding": 100.0}))
        dcf = {"current_price": 10.0, "currency": "NZD", "historical_growth": {}}
        f = fundamentals.compute("T", r, dcf=dcf)
        assert f.peg is None
        assert "peg-no-dcf-growth" in f.reasons

    def test_accepts_the_sdl_historical_cagr_spelling(self):
        r = rows(("FY2025", {"net_income": 100.0, "shares_outstanding": 100.0}))
        dcf = {"current_price": 10.0, "currency": "NZD",
               "historical_growth": {"historical_cagr": 20.0}}
        assert fundamentals.compute("SDL.NZ", r, dcf=dcf).peg == pytest.approx(0.5)


class TestPriceCurrency:
    """WISE.L: price 885.6 is GBP pence, financials are USD.

    `885.6 / 48.43` gives a P/E of 18.3 -- plausible, and wrong. Pence over
    cents only approximates GBP over USD by coincidence.
    """

    def test_pence_price_against_usd_financials_is_rejected(self):
        r = rows(("FY2026", {"net_income": 498.7, "shares_outstanding": 1029.7,
                             "currency": "USD"}))
        dcf = {"current_price": 885.6, "currency": "USD",
               "historical_growth": {"selected_growth_rate": 29.7}}
        f = fundamentals.compute("WISE.L", r, dcf=dcf)
        assert f.peg is None
        assert "price-currency-mismatch" in f.reasons

    def test_matching_currency_is_accepted(self):
        r = rows(("FY2025", {"net_income": 39.5, "shares_outstanding": 59.0,
                             "currency": "NZD"}))
        dcf = {"current_price": 10.65, "currency": "NZD",
               "historical_growth": {"selected_growth_rate": 8.0}}
        f = fundamentals.compute("HLG.NZ", r, dcf=dcf)
        assert f.peg is not None
        assert "price-currency-mismatch" not in f.reasons

    def test_dcf_currency_disagreeing_with_financials_is_rejected(self):
        r = rows(("FY2025", {"net_income": 100.0, "shares_outstanding": 100.0,
                             "currency": "NZD"}))
        dcf = {"current_price": 10.0, "currency": "AUD",
               "historical_growth": {"selected_growth_rate": 10.0}}
        f = fundamentals.compute("T", r, dcf=dcf)
        assert "price-currency-mismatch" in f.reasons


class TestPriorPeriodIsAnchoredToTheOneUsed:
    """The prior TTM must step back from the period the TTM actually used.

    MSFT's real shape: FY2025 is complete, FY2026 has Q1-Q3 only. The TTM
    falls back to FY2025, so the comparison must be FY2024 -- not FY2025
    again, which is what stepping back from the max year (2026) yields and
    which reports a flat 0.000 growth for a company growing ~15%.
    """

    def test_partial_latest_year_does_not_compare_a_value_to_itself(self):
        r = rows(
            ("FY2024", {"revenue": 245122.0}),
            ("FY2025", {"revenue": 281724.0}),
            ("Q1 FY2026", {"revenue": 77673.0}),
            ("Q2 FY2026", {"revenue": 81273.0}),
            ("Q3 FY2026", {"revenue": 82886.0}),
        )
        f = fundamentals.compute("MSFT", r, dcf=None)
        assert f.ttm_revenue == pytest.approx(281724.0)
        assert f.revenue_growth_1y == pytest.approx(281724.0 / 245122.0 - 1)
        assert f.revenue_growth_1y > 0.10


class TestGrowthOffANegativeBase:
    """A growth rate measured from a loss is not a growth rate.

    SEK.NZ's prior-year TTM net income is -7.888 (the FY2023 loss). Dividing
    by it would report a swing from loss to profit as a large positive
    percentage, or flip sign unpredictably. Refusing is correct -- but the
    reason must say so rather than looking like absent data.
    """

    def test_negative_prior_ttm_is_refused_with_a_reason(self):
        r = rows(
            ("FY2023", {"net_income": -14.466}),
            ("H1 FY2023", {"net_income": 10.474}),
            ("FY2024", {"net_income": 8.751}),
            ("H1 FY2024", {"net_income": 17.052}),
            ("H1 FY2025", {"net_income": 37.769}),
            ("FY2025", {"net_income": 31.961}),
        )
        f = fundamentals.compute("SEK.NZ", r, dcf=None)
        # ttm = 8.751 + 37.769 - 17.052 = 29.468 (positive)
        assert f.ttm_net_income == pytest.approx(29.468)
        # prior ttm = -14.466 + 17.052 - 10.474 = -7.888 (negative base)
        assert f.earnings_growth_1y is None
        assert "growth-nonpositive-base:net_income" in f.reasons


class TestUnresolvedUnits:
    """metrics_normalized nulls every money column when units are unknown.

    Reporting that as `no-ttm:revenue` blames the filings for what is really
    an unresolved scale -- AGL.NZ has 18 revenue rows but no units, and the
    fix is backfill_units, not re-extraction.
    """

    def test_all_null_money_with_periods_present_says_units_unresolved(self):
        r = [{"period": "FY2025", "currency": "NZD", "revenue": None,
              "net_income": None, "free_cash_flow": None},
             {"period": "H1 2026", "currency": "NZD", "revenue": None,
              "net_income": None, "free_cash_flow": None}]
        f = fundamentals.compute("AGL.NZ", r, dcf=None)
        assert "units-unresolved" in f.reasons
        assert "no-ttm:revenue" not in f.reasons

    def test_a_genuine_gap_still_reports_no_ttm(self):
        """SUM.NZ has revenue but zero net_income rows -- that IS missing data."""
        r = [{"period": "FY2025", "currency": "NZD", "revenue": 361.8,
              "net_income": None, "free_cash_flow": None}]
        f = fundamentals.compute("SUM.NZ", r, dcf=None)
        assert "units-unresolved" not in f.reasons
        assert "no-ttm:net_income" in f.reasons


class TestTtmBasisIsTheWeakest:
    def test_fy_basis_wins_when_any_field_falls_back(self):
        """Revenue reconstructs, FCF does not -- the row is FY-BASIS."""
        r = rows(
            ("FY2024", {"revenue": 400.0, "free_cash_flow": 40.0}),
            ("FY2025", {"revenue": 470.7, "free_cash_flow": 50.0}),
            ("H1-2025", {"revenue": 240.0}),
            ("H1-2026", {"revenue": 275.2}),
        )
        assert fundamentals.compute("T", r, dcf=None).ttm_basis == "FY"
