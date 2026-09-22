"""scripts/sanity_check.py -- Step 8c of the research-stock skill, in code.

SEK.NZ is the canonical failure this exists to catch: a peak-FCF DCF
produced IV $22.75 implying 30x P/E and 3.4x P/B on a company that has never
traded above 0.99x P/B or 7.8x EV/EBITDA in ten years.

Everything in 8c.1-8c.4 is arithmetic: fetch the FY-end closes, align them
to the annual rows, divide, compare against four thresholds. The
orchestrator (Opus, $1.54/run) was doing it by hand. Only 8c.5 -- deciding
which assumption to change -- is judgment, and that stays with the
dcf-analyst.

Nothing here touches live research/: the DB is built from schema.create_sql()
on tmp_path, and the price series is a fixture.
"""

import datetime as dt
import json
from typing import ClassVar

import duckdb
import pytest
import sanity_check
import schema

# Ten Decembers and ten Junes, so a calendar-year FY and a June FY pick
# demonstrably different anchors out of the same series.
PRICES = [
    (dt.date(2021, 6, 1), 2.00), (dt.date(2021, 12, 1), 3.00),
    (dt.date(2022, 6, 1), 2.50), (dt.date(2022, 12, 1), 4.00),
    (dt.date(2023, 6, 1), 3.50), (dt.date(2023, 12, 1), 5.00),
    (dt.date(2024, 6, 1), 4.50), (dt.date(2024, 12, 1), 6.00),
    (dt.date(2025, 6, 1), 5.50), (dt.date(2025, 12, 1), 7.00),
]

# One clean company: 100 shares, so per-share arithmetic is legible.
ROWS = [
    # period,  revenue, ebitda, net_income, equity, debt, cash, shares, sbc, fcf
    ("FY2022", 500.0, 100.0, 50.0, 400.0, 100.0, 20.0, 100.0, 10.0, 28.0),
    ("FY2023", 550.0, 110.0, 55.0, 440.0, 100.0, 20.0, 100.0, 10.0, 30.0),
    ("FY2024", 600.0, 120.0, 60.0, 480.0, 100.0, 20.0, 100.0, 10.0, 32.0),
    ("FY2025", 650.0, 130.0, 65.0, 520.0, 100.0, 20.0, 100.0, 10.0, 60.0),
]


def make_db(repo, ticker, rows=None, units="millions", extra_periods=()):
    reports = repo / "research" / ticker / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    db = reports / f"{ticker}.duckdb"
    con = duckdb.connect(str(db))
    con.execute(schema.create_sql())
    for r in (rows if rows is not None else ROWS):
        period, rev, ebitda, ni, eq, debt, cash, shares, sbc = r[:9]
        fcf = r[9] if len(r) > 9 else None
        con.execute(
            "INSERT INTO core_metrics (period, revenue, ebitda, net_income, "
            "shareholders_equity, total_debt, cash_and_equivalents, "
            "shares_outstanding, stock_based_comp, free_cash_flow, units, "
            "currency) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [period, rev, ebitda, ni, eq, debt, cash, shares, sbc, fcf,
             units, "NZD"])
    for period in extra_periods:
        con.execute(
            "INSERT INTO core_metrics (period, revenue, units) VALUES (?,?,?)",
            [period, 300.0, units])
    con.close()
    return db


def make_dcf(repo, ticker, intrinsic_value=8.0, **inputs):
    reports = repo / "research" / ticker / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{ticker}_DCF.json"
    doc = {
        "ticker": ticker,
        "current_price": 5.0,
        "currency": "NZD",
        "inputs": {"currency": "NZD", "quote_currency": "NZD",
                   "shares_outstanding": 100.0, "last_fcf": 60.0,
                   "total_debt": 100.0, "cash": 20.0, **inputs},
        "valuation": {"base": {"intrinsic_value": intrinsic_value,
                               "upside": 60.0}},
    }
    path.write_text(json.dumps(doc, indent=2))
    return path


def make_prices(repo, ticker, rows=PRICES):
    reports = repo / "research" / ticker / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{ticker}_Prices.csv"
    lines = ["Date,Close"]
    lines += [f"{d.isoformat()},{c}" for d, c in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


# The whole check runs "as of" this date, so the fixture series stays fresh
# no matter when the suite runs -- a cache pinned to fixed calendar dates
# would start hitting the network the day the wall clock passed it.
TODAY = dt.date(2025, 12, 20)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "research").mkdir()
    monkeypatch.setattr(sanity_check, "REPO", tmp_path)
    # No test may reach the network; a monthly() call that got through
    # would silently make the suite depend on Yahoo being up.
    monkeypatch.setattr(sanity_check.quotes, "monthly",
                        lambda *a, **k: pytest.fail("network in a test"))
    return tmp_path


class TestPriceCache:
    """The 10y monthly series is committed alongside the metrics.

    120 rows of Date,Close is 2KB, and it makes the whole check
    reproducible offline -- and reviewable: a wrong FY anchor is visible in
    the diff rather than buried in a fetch nobody can replay.
    """

    def test_a_fresh_cache_is_reused_without_fetching(self, repo):
        make_prices(repo, "SEK.NZ", [
            *PRICES[:-1], (TODAY - dt.timedelta(days=5), 7.0)])
        rows = sanity_check.price_series(repo, "SEK.NZ", today=TODAY)
        assert len(rows) == len(PRICES)

    def test_a_stale_cache_is_refetched(self, repo, monkeypatch):
        make_prices(repo, "SEK.NZ", [(dt.date(2019, 1, 1), 1.0)])
        fresh = [(dt.date(2025, 12, 1), 7.0)]
        monkeypatch.setattr(sanity_check.quotes, "monthly", lambda *a, **k: fresh)
        rows = sanity_check.price_series(repo, "SEK.NZ")
        assert rows == fresh

    def test_a_refetch_rewrites_the_cache_file(self, repo, monkeypatch):
        make_prices(repo, "SEK.NZ", [(dt.date(2019, 1, 1), 1.0)])
        monkeypatch.setattr(sanity_check.quotes, "monthly",
                            lambda *a, **k: [(dt.date(2025, 12, 1), 7.0)])
        sanity_check.price_series(repo, "SEK.NZ")
        text = (repo / "research" / "SEK.NZ" / "Reports"
                / "SEK.NZ_Prices.csv").read_text()
        assert text.startswith("Date,Close")
        # 6 significant figures, trailing zeros trimmed -- 7.0 is "7".
        assert "2025-12-01,7" in text
        assert sanity_check._read_cache(
            repo / "research" / "SEK.NZ" / "Reports"
            / "SEK.NZ_Prices.csv") == [(dt.date(2025, 12, 1), 7.0)]

    def test_the_cache_is_written_at_a_readable_precision(self, repo, monkeypatch):
        """Yahoo returns 129.55999755859375. The file is committed and read
        by humans in diffs, and no multiple in this script is sensitive to
        the eleventh decimal place of a share price."""
        monkeypatch.setattr(sanity_check.quotes, "monthly", lambda *a, **k: [
            (dt.date(2025, 12, 1), 129.55999755859375)])
        sanity_check.price_series(repo, "SEK.NZ")
        text = (repo / "research" / "SEK.NZ" / "Reports"
                / "SEK.NZ_Prices.csv").read_text()
        assert "2025-12-01,129.56" in text
        assert "129.5599975" not in text

    def test_a_sub_cent_price_keeps_its_significant_digits(self, repo, monkeypatch):
        """BGI.NZ traded at $0.004 and CBD.NZ lower still. Rounding to two
        decimals would turn a real price into zero and every multiple
        derived from it into nonsense."""
        monkeypatch.setattr(sanity_check.quotes, "monthly", lambda *a, **k: [
            (dt.date(2025, 12, 1), 0.00063123456)])
        sanity_check.price_series(repo, "SEK.NZ")
        text = (repo / "research" / "SEK.NZ" / "Reports"
                / "SEK.NZ_Prices.csv").read_text()
        assert "0.00063" in text
        assert ",0.0\n" not in text

    def test_a_failed_refetch_keeps_the_stale_cache(self, repo, monkeypatch):
        """A blank series would compute every multiple as None and report a
        clean pass. Stale prices are wrong; no prices is worse."""
        make_prices(repo, "SEK.NZ", [(dt.date(2019, 1, 1), 1.0)])
        monkeypatch.setattr(sanity_check.quotes, "monthly", lambda *a, **k: [])
        assert sanity_check.price_series(repo, "SEK.NZ") == [(dt.date(2019, 1, 1), 1.0)]

    def test_no_cache_and_no_network_is_empty(self, repo, monkeypatch):
        monkeypatch.setattr(sanity_check.quotes, "monthly", lambda *a, **k: [])
        assert sanity_check.price_series(repo, "SEK.NZ") == []


class TestFiscalYearEnd:
    def test_info_json_fiscal_year_end_wins(self, repo):
        d = repo / "research" / "SEK.NZ"
        d.mkdir(parents=True)
        (d / "info.json").write_text(json.dumps({"fiscal_year_end": "06-30"}))
        month, source = sanity_check.fy_end_month(repo, "SEK.NZ")
        assert month == 6
        assert "info.json" in source

    def test_learned_from_the_annual_filings_when_info_is_silent(self, repo):
        d = repo / "research" / "SEK.NZ" / "Extracted"
        d.mkdir(parents=True)
        (d / "SEK.NZ_Annual_FY2025.txt").write_text(
            "Consolidated Income Statement\n"
            "For the year ended 30 June 2025\n"
            "Consolidated Statement of Comprehensive Income\n"
            "For the year ended 30 June 2025\n"
            "Consolidated Statement of Cash Flows\n"
            "For the year ended 30 June 2025\n")
        month, source = sanity_check.fy_end_month(repo, "SEK.NZ")
        assert month == 6
        assert "filing" in source

    def test_december_fallback_says_so(self, repo):
        (repo / "research" / "SEK.NZ").mkdir(parents=True)
        month, source = sanity_check.fy_end_month(repo, "SEK.NZ")
        assert month == 12
        assert "fallback" in source

    def test_a_malformed_fiscal_year_end_falls_back(self, repo):
        d = repo / "research" / "SEK.NZ"
        d.mkdir(parents=True)
        (d / "info.json").write_text(json.dumps({"fiscal_year_end": "sometime"}))
        month, _ = sanity_check.fy_end_month(repo, "SEK.NZ")
        assert month == 12


class TestFyEndPriceAlignment:
    def test_a_june_fy_takes_the_june_close_of_that_year(self):
        assert sanity_check.fy_end_price(PRICES, 2024, 6) == 4.50
        assert sanity_check.fy_end_price(PRICES, 2023, 6) == 3.50

    def test_a_december_fy_takes_the_december_close(self):
        assert sanity_check.fy_end_price(PRICES, 2024, 12) == 6.00

    def test_a_monthly_bar_is_stamped_at_the_month_start(self):
        """Yahoo stamps the 1mo bar at the FIRST of the month and fills it
        with that month's close, so December 2024's bar is 2024-12-01 and
        carries the 31-Dec price. Matching on year+month is what makes a
        December FY-end pick December's close rather than January's."""
        series = [(dt.date(2024, 12, 1), 6.0), (dt.date(2025, 1, 1), 8.0)]
        assert sanity_check.fy_end_price(series, 2024, 12) == 6.0

    def test_a_partial_current_month_bar_does_not_displace_the_close(self):
        """Yahoo appends a second, part-formed bar for the month in
        progress -- DUOL's series ends 2026-09-01 and 2026-09-04 on the
        same day's price. The completed month's bar is the FY anchor; a
        mid-month quote is not the year's closing price."""
        series = [(dt.date(2024, 12, 1), 6.0), (dt.date(2024, 12, 19), 9.0)]
        assert sanity_check.fy_end_price(series, 2024, 12) == 6.0

    def test_a_year_outside_the_series_is_none(self):
        assert sanity_check.fy_end_price(PRICES, 2010, 12) is None

    def test_a_missing_month_falls_back_to_the_nearest_earlier_close(self):
        """Yahoo drops months a stock did not trade. The FY-end anchor then
        comes from the last close before it, never from a later one -- a
        price the market had not yet set cannot value that year."""
        series = [(dt.date(2024, 3, 1), 4.0), (dt.date(2024, 9, 1), 9.0)]
        assert sanity_check.fy_end_price(series, 2024, 6) == 4.0

    def test_the_fallback_will_not_reach_back_more_than_a_quarter(self):
        series = [(dt.date(2023, 1, 1), 4.0), (dt.date(2024, 9, 1), 9.0)]
        assert sanity_check.fy_end_price(series, 2024, 6) is None


class TestAnnualRows:
    def test_only_genuine_twelve_month_years_are_used(self, repo):
        """FY2017-15mo and FY2018-6moStub are FY-shaped but are not years;
        periods.is_annual is what says so."""
        make_db(repo, "SEK.NZ",
                extra_periods=("H1 FY2025", "FY2019-6moStub", "Q1 2025"))
        rows = sanity_check.annual_rows(repo, "SEK.NZ")
        assert [r["period"] for r in rows] == ["FY2022", "FY2023", "FY2024", "FY2025"]

    def test_values_come_back_in_millions(self, repo):
        make_db(repo, "SEK.NZ", units="thousands")
        rows = sanity_check.annual_rows(repo, "SEK.NZ")
        assert rows[-1]["revenue"] == pytest.approx(0.650)

    def test_unknown_units_leave_the_row_null_not_guessed(self, repo):
        """SEK.NZ once read as NZ$411bn of revenue from a defaulted
        thousands->millions guess. A missing row is obvious."""
        make_db(repo, "SEK.NZ", units=None)
        rows = sanity_check.annual_rows(repo, "SEK.NZ")
        assert all(r["revenue"] is None for r in rows)

    def test_the_csv_is_the_fallback_when_the_db_is_absent(self, repo):
        reports = repo / "research" / "SEK.NZ" / "Reports"
        reports.mkdir(parents=True)
        (reports / "SEK.NZ_Metrics.csv").write_text(
            "Period,Revenue,EBITDA,NetIncome,ShareholdersEquity,TotalDebt,"
            "CashAndEquivalents,SharesOutstanding,StockBasedComp\n"
            "FY2024,600,120,60,480,100,20,100,10\n"
            "H1 2025,300,60,30,490,100,20,100,5\n")
        rows = sanity_check.annual_rows(repo, "SEK.NZ")
        assert [r["period"] for r in rows] == ["FY2024"]
        assert rows[0]["ebitda"] == 120.0


class TestHistoricalMultiples:
    """8c.1, on the SBC-adjusted basis, both sides."""

    def hist(self, repo, **kw):
        make_db(repo, "SEK.NZ", **kw)
        make_prices(repo, "SEK.NZ")
        return sanity_check.historical_table(
            sanity_check.annual_rows(repo, "SEK.NZ"), PRICES, 12)

    def test_pe_is_on_eps_less_sbc_per_share(self, repo):
        # FY2024: NI 60, SBC 10, shares 100 -> adjusted EPS 0.50; price 6.00
        row = {r["period"]: r for r in self.hist(repo)}["FY2024"]
        assert row["pe"] == pytest.approx(12.0)

    def test_pb_is_market_cap_over_equity(self, repo):
        # FY2024: 6.00 x 100 = 600 over equity 480
        row = {r["period"]: r for r in self.hist(repo)}["FY2024"]
        assert row["pb"] == pytest.approx(1.25)

    def test_ev_ebitda_uses_net_debt_and_sbc_adjusted_ebitda(self, repo):
        # EV = 600 + (100 - 20) = 680; EBITDA - SBC = 120 - 10 = 110
        row = {r["period"]: r for r in self.hist(repo)}["FY2024"]
        assert row["ev_ebitda"] == pytest.approx(680 / 110, abs=1e-4)

    def test_p_sales_is_market_cap_over_revenue(self, repo):
        row = {r["period"]: r for r in self.hist(repo)}["FY2024"]
        assert row["p_sales"] == pytest.approx(1.0)

    def test_a_negative_adjusted_eps_year_has_no_pe(self, repo):
        rows = [("FY2024", 600.0, 120.0, 5.0, 480.0, 100.0, 20.0, 100.0, 40.0)]
        row = self.hist(repo, rows=rows)[0]
        assert row["pe"] is None
        assert row["pb"] is not None       # the other multiples survive

    def test_sbc_null_makes_the_adjusted_multiples_none(self, repo):
        """Never silently mix adjusted and unadjusted years: comparing an
        SBC-adjusted implied multiple against a half-unadjusted average
        manufactures a false trip (or hides a real one)."""
        rows = [("FY2024", 600.0, 120.0, 60.0, 480.0, 100.0, 20.0, 100.0, None)]
        row = self.hist(repo, rows=rows)[0]
        assert row["pe"] is None
        assert row["ev_ebitda"] is None
        # P/B and P/Sales do not touch earnings, so SBC cannot affect them.
        assert row["pb"] == pytest.approx(1.25)
        assert row["p_sales"] == pytest.approx(1.0)

    def test_a_year_with_no_price_is_carried_with_nulls(self, repo):
        row = {r["period"]: r for r in self.hist(repo)}["FY2022"]
        assert row["price"] == 4.00
        rows = sanity_check.historical_table(
            [{"period": "FY2010", "revenue": 1.0, "net_income": 1.0,
              "ebitda": 1.0, "shareholders_equity": 1.0, "total_debt": 0.0,
              "cash_and_equivalents": 0.0, "shares_outstanding": 1.0,
              "stock_based_comp": 0.0}], PRICES, 12)
        assert rows[0]["price"] is None
        assert rows[0]["pe"] is None

    def test_a_near_breakeven_year_has_no_meaningful_pe(self, repo):
        """MCY.NZ earned NZ$1m on 1,400m shares in FY2025 -- an adjusted
        EPS of $0.00026 and a P/E of 23,958x. The division is correct and
        the number is meaningless: it dragged the 10yr average to 3,028x
        and then tripped the rule against its own garbage.

        A P/E is only informative while the denominator is a real earnings
        base, so a year yielding an absurd multiple is dropped from the
        table exactly as a loss-making year already was.
        """
        rows = [("FY2025", 650.0, 130.0, 1.0, 520.0, 100.0, 20.0, 1400.0, 0.6)]
        row = self.hist(repo, rows=rows)[0]
        assert row["pe"] is None
        assert row["pb"] is not None       # book value is unaffected

    def test_an_ordinary_high_pe_still_counts(self, repo):
        """The cutoff must not quietly discard real expensive years -- a
        growth company at 80x is information, not noise."""
        rows = [("FY2025", 650.0, 130.0, 20.0, 520.0, 100.0, 20.0, 100.0, 10.0)]
        # adjusted EPS = (20 - 10) / 100 = 0.10; price 7.00 -> 70x
        row = self.hist(repo, rows=rows)[0]
        assert row["pe"] == pytest.approx(70.0, abs=1e-3)

    def test_the_implied_multiple_uses_the_same_cutoff(self, repo):
        """Otherwise an implied 26,878x gets compared against a historical
        average that correctly excluded its own equivalent."""
        latest = {"period": "FY2025", "revenue": 650.0, "ebitda": 130.0,
                  "net_income": 1.0, "shareholders_equity": 520.0,
                  "total_debt": 100.0, "cash_and_equivalents": 20.0,
                  "shares_outstanding": 1400.0, "stock_based_comp": 0.6}
        assert sanity_check.implied_multiples(7.0, latest)["pe"] is None

    def test_zero_ebitda_does_not_divide_by_zero(self, repo):
        rows = [("FY2024", 600.0, 10.0, 60.0, 480.0, 100.0, 20.0, 100.0, 10.0)]
        assert self.hist(repo, rows=rows)[0]["ev_ebitda"] is None


class TestAverages:
    def test_averages_and_maxima_skip_nulls(self):
        table = [{"pe": 10.0, "pb": 0.5, "ev_ebitda": 5.0, "p_sales": 1.0},
                 {"pe": None, "pb": 0.9, "ev_ebitda": 7.0, "p_sales": 2.0},
                 {"pe": 20.0, "pb": 0.7, "ev_ebitda": None, "p_sales": None}]
        avg = sanity_check.averages(table)
        assert avg["pe_avg"] == pytest.approx(15.0)
        assert avg["pb_max"] == pytest.approx(0.9)
        assert avg["ev_ebitda_max"] == pytest.approx(7.0)
        assert avg["p_sales_avg"] == pytest.approx(1.5)

    def test_an_all_null_column_is_none_not_zero(self):
        avg = sanity_check.averages([{"pe": None, "pb": None,
                                      "ev_ebitda": None, "p_sales": None}])
        assert avg["pe_avg"] is None
        assert avg["pb_max"] is None


class TestImpliedMultiples:
    """8c.2 -- what the base IV would imply on the latest FY actuals."""

    def test_implied_pe_pb_and_ev_ebitda(self, repo):
        latest = {"period": "FY2025", "revenue": 650.0, "ebitda": 130.0,
                  "net_income": 65.0, "shareholders_equity": 520.0,
                  "total_debt": 100.0, "cash_and_equivalents": 20.0,
                  "shares_outstanding": 100.0, "stock_based_comp": 10.0}
        imp = sanity_check.implied_multiples(8.0, latest)
        # adjusted EPS = (65 - 10) / 100 = 0.55
        assert imp["pe"] == pytest.approx(8.0 / 0.55, abs=1e-4)
        assert imp["pb"] == pytest.approx(8.0 / 5.20, abs=1e-4)
        # EV = 8 x 100 + 80 = 880; EBITDA - SBC = 120
        assert imp["ev_ebitda"] == pytest.approx(880 / 120, abs=1e-4)
        assert imp["p_sales"] == pytest.approx(800 / 650, abs=1e-4)

    def test_sbc_null_gives_no_earnings_multiples(self, repo):
        latest = {"period": "FY2025", "revenue": 650.0, "ebitda": 130.0,
                  "net_income": 65.0, "shareholders_equity": 520.0,
                  "total_debt": 100.0, "cash_and_equivalents": 20.0,
                  "shares_outstanding": 100.0, "stock_based_comp": None}
        imp = sanity_check.implied_multiples(8.0, latest)
        assert imp["pe"] is None
        assert imp["ev_ebitda"] is None
        assert imp["pb"] is not None


class TestTripRules:
    """8c.3, each at its boundary. The thresholds are the spec's, and a
    check that fires one cent early is a check nobody trusts."""

    HIST: ClassVar[dict] = {"pe_avg": 10.0, "pb_max": 1.0,
                            "ev_ebitda_max": 8.0}

    def trips(self, implied, hist=None, upside=0.0, price_pos=0.0, rules=None):
        return sanity_check.trip_reasons(implied, hist or self.HIST,
                                         upside, price_pos, rules=rules)

    def test_pe_at_exactly_two_times_the_average_does_not_trip(self):
        assert self.trips({"pe": 20.0}) == []

    def test_pe_just_over_two_times_the_average_trips(self):
        reasons = self.trips({"pe": 20.01})
        assert len(reasons) == 1
        assert "P/E" in reasons[0]

    def test_pb_at_exactly_one_and_a_half_times_the_max_does_not_trip(self):
        assert self.trips({"pb": 1.5}) == []

    def test_pb_just_over_trips(self):
        assert "P/B" in self.trips({"pb": 1.51})[0]

    def test_ev_ebitda_at_exactly_one_and_a_half_times_the_max_holds(self):
        assert self.trips({"ev_ebitda": 12.0}) == []

    def test_ev_ebitda_just_over_trips(self):
        assert "EV/EBITDA" in self.trips({"ev_ebitda": 12.01})[0]

    def test_a_rule_set_restricts_which_rules_can_trip(self):
        # AFFO family: P/B and the upside/position rule only.
        rules = sanity_check.FAMILY_RULES["affo"]
        assert self.trips({"pe": 99.0, "ev_ebitda": 99.0, "pb": 1.5}, rules=rules) == []
        assert "P/B" in self.trips({"pe": 99.0, "pb": 1.51}, rules=rules)[0]
        # Funds managers and banks on distributable earnings: P/E and P/B.
        rules = sanity_check.FAMILY_RULES["earnings_at_coe"]
        assert "P/E" in self.trips({"pe": 20.01, "ev_ebitda": 99.0}, rules=rules)[0]
        assert sanity_check.FAMILY_RULES["owner_fcf"] == ("pe", "pb", "ev_ebitda", "p_sales", "upside_position")
        assert "nav" in sanity_check.FAMILY_RULES
        assert "risked_npv" not in sanity_check.FAMILY_RULES

    def test_upside_over_100_in_the_upper_half_of_the_range_trips(self):
        assert self.trips({}, upside=100.1, price_pos=0.51) != []

    def test_upside_over_100_in_the_lower_half_does_not_trip(self):
        """The rule is upside AND an already-elevated price. A cheap stock
        with big upside is the thing the screener is FOR."""
        assert self.trips({}, upside=250.0, price_pos=0.49) == []

    def test_upside_at_exactly_100_does_not_trip(self):
        assert self.trips({}, upside=100.0, price_pos=0.9) == []

    def test_a_missing_implied_multiple_cannot_trip(self):
        assert self.trips({"pe": None, "pb": None, "ev_ebitda": None}) == []

    def test_a_missing_historical_average_cannot_trip(self):
        assert self.trips({"pe": 99.0}, hist={"pe_avg": None}) == []

    def test_every_rule_can_fire_at_once(self):
        reasons = self.trips({"pe": 30.0, "pb": 3.4, "ev_ebitda": 20.0},
                             upside=354.0, price_pos=0.8)
        assert len(reasons) == 4


class TestPricePosition:
    def test_position_in_the_ten_year_range(self):
        assert sanity_check.price_position(5.0, PRICES) == pytest.approx(0.6)

    def test_a_flat_series_is_the_midpoint_not_a_divide_by_zero(self):
        assert sanity_check.price_position(
            3.0, [(dt.date(2024, 1, 1), 3.0)]) == 0.5

    def test_an_empty_series_is_none(self):
        assert sanity_check.price_position(3.0, []) is None


class TestDiagnosis:
    """8c.4 -- candidates, ranked. The script names suspects; picking the
    fix (8c.5) stays the dcf-analyst's judgment."""

    def test_peak_fcf_is_flagged_above_one_and_a_half_times_the_median(self):
        cands = sanity_check.diagnose(
            {"last_fcf": 79.0}, fcf_history=[20.0, 30.0, 32.0, 34.0, 45.0],
            risks=[], growth_pct=None, terminal_growth=None)
        assert any(c["candidate"] == "peak_fcf" for c in cands)
        peak = next(c for c in cands if c["candidate"] == "peak_fcf")
        assert peak["median"] == pytest.approx(32.0)

    def test_at_exactly_one_and_a_half_times_the_median_it_is_not_a_peak(self):
        cands = sanity_check.diagnose(
            {"last_fcf": 48.0}, fcf_history=[20.0, 30.0, 32.0, 34.0, 45.0],
            risks=[], growth_pct=None, terminal_growth=None)
        assert not any(c["candidate"] == "peak_fcf" for c in cands)

    def test_structural_risk_keywords_are_surfaced(self):
        cands = sanity_check.diagnose(
            {}, fcf_history=[], risks=[
                {"risk": "Zespri dependency and single-desk structure",
                 "description": "One customer takes 90% of revenue"}],
            growth_pct=None, terminal_growth=None)
        wacc = next(c for c in cands if c["candidate"] == "wacc_too_low")
        assert any("concentration" in k for k in wacc["keywords"])

    def test_risks_as_a_dict_of_lists_are_read_too(self):
        """Analysis.json nests risks under business/financial/regulatory."""
        cands = sanity_check.diagnose(
            {}, fcf_history=[], risks={"business": [
                {"risk": "Extreme cyclicality in kiwifruit volumes"}]},
            growth_pct=None, terminal_growth=None)
        assert any(c["candidate"] == "wacc_too_low" for c in cands)

    def test_no_structural_keywords_means_no_wacc_candidate(self):
        cands = sanity_check.diagnose(
            {}, fcf_history=[], risks=[{"risk": "Competition from peers"}],
            growth_pct=None, terminal_growth=None)
        assert not any(c["candidate"] == "wacc_too_low" for c in cands)

    def test_growth_above_the_through_cycle_cagr_is_flagged(self):
        cands = sanity_check.diagnose(
            {}, fcf_history=[], risks=[], growth_pct=12.0,
            terminal_growth=None, revenue_cagr_pct=4.0)
        growth = next(c for c in cands if c["candidate"] == "growth_too_high")
        assert growth["projected_pct"] == 12.0
        assert growth["through_cycle_cagr_pct"] == 4.0

    def test_growth_at_the_cagr_is_not_flagged(self):
        cands = sanity_check.diagnose(
            {}, fcf_history=[], risks=[], growth_pct=4.0,
            terminal_growth=None, revenue_cagr_pct=4.0)
        assert not any(c["candidate"] == "growth_too_high" for c in cands)

    def test_terminal_growth_above_three_percent_is_flagged(self):
        cands = sanity_check.diagnose({}, fcf_history=[], risks=[],
                                      growth_pct=None, terminal_growth=0.035)
        assert any(c["candidate"] == "terminal_growth_too_high" for c in cands)

    def test_terminal_growth_at_three_percent_is_not(self):
        cands = sanity_check.diagnose({}, fcf_history=[], risks=[],
                                      growth_pct=None, terminal_growth=0.03)
        assert not any(c["candidate"] == "terminal_growth_too_high"
                       for c in cands)

    def test_terminal_growth_given_as_a_percent_is_understood(self):
        """The corpus stores it both ways: 0.025 and 2.5."""
        cands = sanity_check.diagnose({}, fcf_history=[], risks=[],
                                      growth_pct=None, terminal_growth=3.5)
        assert any(c["candidate"] == "terminal_growth_too_high" for c in cands)


class TestThroughCycleCagr:
    """8c.4.3 asks whether projected growth beats the through-cycle CAGR --
    first genuine year to last, not the cyclical recovery window."""

    def test_cagr_spans_the_whole_history(self):
        rows = [{"period": "FY2020", "revenue": 100.0},
                {"period": "FY2025", "revenue": 200.0}]
        # 2x over five years is 14.87%/yr.
        got = sanity_check._revenue_cagr_pct(rows)
        assert got == pytest.approx(14.87, abs=0.01)

    def test_a_gap_in_the_years_is_measured_end_to_end(self):
        """The count of rows is not the span in years. ARB.NZ's stub years
        and any ticker missing a filing leave holes, and measuring
        `len(rows) - 1` years back from the last one lands on a year that
        does not exist -- silently returning no CAGR at all.
        """
        rows = [{"period": "FY2015", "revenue": 100.0},
                {"period": "FY2020", "revenue": 150.0},
                {"period": "FY2025", "revenue": 200.0}]
        got = sanity_check._revenue_cagr_pct(rows)
        assert got is not None
        assert got == pytest.approx(7.18, abs=0.01)      # 10 years, not 2

    def test_a_single_year_has_no_cagr(self):
        assert sanity_check._revenue_cagr_pct(
            [{"period": "FY2025", "revenue": 200.0}]) is None

    def test_a_nonpositive_base_has_no_cagr(self):
        rows = [{"period": "FY2020", "revenue": 0.0},
                {"period": "FY2025", "revenue": 200.0}]
        assert sanity_check._revenue_cagr_pct(rows) is None

    def test_non_annual_rows_do_not_define_the_span(self):
        rows = [{"period": "FY2020", "revenue": 100.0},
                {"period": "H1 2023", "revenue": 60.0},
                {"period": "FY2025", "revenue": 200.0}]
        assert sanity_check._revenue_cagr_pct(rows) == pytest.approx(14.87,
                                                                     abs=0.01)


class TestNonFcfModels:
    """A REIT has no EBITDA multiple worth the name and an LIC has no
    earnings at all. Grading them against P/E and EV/EBITDA thresholds
    produces a verdict about arithmetic that was never performed."""

    @pytest.mark.parametrize(("label", "expected"), [
        ("AFFO capitalization at cost of equity", "affo"),
        ("NAV-per-unit compounding with exit price-to-NAV", "nav"),
        ("Book-value compounding (BVPS growth x exit Price/Book)", "book_value"),
        ("residual income on tangible book", "book_value"),
        ("residual_income_on_tangible_book", "book_value"),
        ("discounted_distributable_earnings_at_cost_of_equity", "earnings_at_coe"),
        ("risked project-NPV, pre-revenue gold developer", "risked_npv"),
        ("asset_scenario_shell_value", "asset_waterfall"),
        ("owner-fcf-dcf", "owner_fcf"),
        ("Owner-FCF component DCF on a strict statutory basis", "owner_fcf"),
    ])
    def test_model_family_is_read_from_the_dcf(self, label, expected):
        assert sanity_check.model_family({"valuation_model": label}) == expected

    @pytest.mark.parametrize("key", ["model", "model_type", "method",
                                     "approach", "methodology"])
    def test_valuation_philosophy_states_the_model_under_any_of_its_keys(self, key):
        """ARG.NZ declares its REIT model ONLY in
        valuation_philosophy.model_type -- "AFFO-capitalization /
        cost-of-equity model (REIT), NOT a WACC-based owner-FCF DCF" -- and
        still carries inputs.last_fcf. Scanning only some of the subkeys
        graded it against P/E and EV/EBITDA thresholds it was never built
        to satisfy.
        """
        doc = {"valuation_philosophy": {key: "AFFO capitalization at cost of equity"},
               "inputs": {"last_fcf": 58.818}}
        assert sanity_check.model_family(doc) == "affo"

    def test_a_philosophy_that_disclaims_owner_fcf_is_not_owner_fcf(self):
        """The label says what the model IS; a sentence that names owner-FCF
        only to deny it must not be read as claiming it."""
        doc = {"valuation_philosophy": {"model_type": (
            "AFFO-capitalization / cost-of-equity model (REIT), NOT a "
            "WACC-based owner-FCF DCF")},
            "inputs": {"last_fcf": 58.818}}
        assert sanity_check.model_family(doc) == "affo"

    def test_an_unlabelled_dcf_with_last_fcf_is_owner_fcf(self):
        assert sanity_check.model_family(
            {"inputs": {"last_fcf": 60.0}}) == "owner_fcf"

    def test_an_unlabelled_dcf_without_last_fcf_is_unknown(self):
        assert sanity_check.model_family({"inputs": {}}) == "unknown"

    def test_a_replaced_model_gets_a_null_verdict_and_a_note(self, repo):
        make_db(repo, "ARG.NZ")
        make_prices(repo, "ARG.NZ")
        make_dcf(repo, "ARG.NZ")
        path = repo / "research" / "ARG.NZ" / "Reports" / "ARG.NZ_DCF.json"
        doc = json.loads(path.read_text())
        doc["valuation_model"] = "AFFO capitalization at cost of equity"
        path.write_text(json.dumps(doc, indent=2))
        block = sanity_check.check(repo, "ARG.NZ", today=TODAY)
        assert block["ran"] is True
        # A REIT is graded on P/B against its own decade, never on P/E or
        # EV/EBITDA: statutory NPAT is revaluation noise and EBITDA undefined.
        assert block["model"] == "affo"
        assert block["passed"] is True
        assert block["rules_evaluated"] == ["pb"]
        assert set(block["rules_not_applicable"]) == {"pe", "ev_ebitda", "p_sales"}
        assert "checks_replaced" not in block

    def test_a_reit_trips_on_price_to_book_alone(self, repo):
        make_db(repo, "ARG.NZ")
        make_prices(repo, "ARG.NZ")
        make_dcf(repo, "ARG.NZ", intrinsic_value=80.0)      # 10x the fixture's IV
        path = repo / "research" / "ARG.NZ" / "Reports" / "ARG.NZ_DCF.json"
        doc = json.loads(path.read_text())
        doc["valuation_model"] = "AFFO capitalization at cost of equity"
        path.write_text(json.dumps(doc, indent=2))
        block = sanity_check.check(repo, "ARG.NZ", today=TODAY)
        assert block["passed"] is False
        assert len(block["trip_reasons"]) >= 1
        assert all("P/B" in r or "upside" in r for r in block["trip_reasons"])

    def test_models_with_no_multiple_to_grade_keep_the_null_verdict(self, repo):
        make_db(repo, "SMI.NZ")
        make_prices(repo, "SMI.NZ")
        make_dcf(repo, "SMI.NZ")
        path = repo / "research" / "SMI.NZ" / "Reports" / "SMI.NZ_DCF.json"
        doc = json.loads(path.read_text())
        doc["valuation_model"] = "risked project NPV"
        path.write_text(json.dumps(doc, indent=2))
        block = sanity_check.check(repo, "SMI.NZ", today=TODAY)
        assert block["passed"] is None
        assert "risked_npv" in block["checks_replaced"]

    def test_a_replaced_model_still_reports_the_history_it_could_compute(self, repo):
        make_db(repo, "ARG.NZ")
        make_prices(repo, "ARG.NZ")
        make_dcf(repo, "ARG.NZ")
        path = repo / "research" / "ARG.NZ" / "Reports" / "ARG.NZ_DCF.json"
        doc = json.loads(path.read_text())
        doc["valuation_model"] = "NAV + exit discount"
        path.write_text(json.dumps(doc, indent=2))
        block = sanity_check.check(repo, "ARG.NZ", today=TODAY)
        assert block["historical_table"]
        assert block["trip_reasons"] == []


class TestCurrencyGuard:
    """WISE.L quotes GBP pence against USD filings. 885.6 / 48.43 is a
    plausible P/E of 18.3 and pure coincidence."""

    def test_a_mismatch_without_an_fx_rate_is_refused(self, repo):
        make_db(repo, "WISE.L")
        make_prices(repo, "WISE.L")
        make_dcf(repo, "WISE.L", currency="USD", quote_currency="GBp")
        with pytest.raises(sanity_check.DenominationError):
            sanity_check.check(repo, "WISE.L", today=TODAY)

    def test_the_cli_exits_2_on_a_mismatch(self, repo, capsys):
        make_db(repo, "WISE.L")
        make_prices(repo, "WISE.L")
        make_dcf(repo, "WISE.L", currency="USD", quote_currency="GBp")
        assert sanity_check.main(["WISE.L", "--as-of", TODAY.isoformat()]) == 2
        assert "refused" in capsys.readouterr().err.lower()

    def test_the_fx_rate_is_quote_to_reporting(self, repo):
        """Direction is the whole point of the field. The price is quoted
        in quote_currency and has to end up denominated like the
        financials, so fx_rate multiplies quote -> reporting. Backwards is
        a silent 100x on precisely the ticker the guard exists for.
        """
        make_db(repo, "WISE.L")
        make_prices(repo, "WISE.L")
        # 6.00 GBp is 0.075 USD at 0.0125 USD per penny, not 480.
        make_dcf(repo, "WISE.L", currency="USD", quote_currency="GBp",
                 fx_rate=0.0125)
        block = sanity_check.check(repo, "WISE.L", today=TODAY)
        row = {r["period"]: r for r in block["historical_table"]}["FY2024"]
        assert row["price"] == pytest.approx(0.075)
        assert row["price"] < 1.0            # not multiplied the wrong way

    def test_the_refusal_says_which_direction_the_rate_must_be(self, repo):
        make_db(repo, "9988.HK")
        make_prices(repo, "9988.HK")
        make_dcf(repo, "9988.HK", currency="RMB", quote_currency="HKD")
        with pytest.raises(sanity_check.DenominationError) as e:
            sanity_check.check(repo, "9988.HK", today=TODAY)
        assert "HKD->RMB" in str(e.value)

    def test_an_fx_rate_converts_the_price_instead_of_refusing(self, repo):
        make_db(repo, "WISE.L")
        make_prices(repo, "WISE.L")
        make_dcf(repo, "WISE.L", currency="USD", quote_currency="GBp",
                 fx_rate=0.0125)
        block = sanity_check.check(repo, "WISE.L", today=TODAY)
        # FY2024 close 6.00 GBp x 0.0125 = 0.075 USD
        row = {r["period"]: r for r in block["historical_table"]}["FY2024"]
        assert row["price"] == pytest.approx(0.075)

    def test_the_refusal_names_this_ticker_s_own_currencies(self, repo):
        """The message is read by whoever has to fix the DCF, and the
        HK-listed China filers (RMB financials, HKD quote) are a whole
        class of it. A refusal that only ever recites WISE.L's pence
        reads as boilerplate about a different ticker."""
        make_db(repo, "9988.HK")
        make_prices(repo, "9988.HK")
        make_dcf(repo, "9988.HK", currency="RMB", quote_currency="HKD")
        with pytest.raises(sanity_check.DenominationError) as e:
            sanity_check.check(repo, "9988.HK", today=TODAY)
        assert "HKD" in str(e.value)
        assert "RMB" in str(e.value)
        assert "fx_rate" in str(e.value)
        assert "885.6" not in str(e.value)

    def test_matching_currencies_need_no_fx_rate(self, repo):
        make_db(repo, "SEK.NZ")
        make_prices(repo, "SEK.NZ")
        make_dcf(repo, "SEK.NZ")
        assert sanity_check.check(repo, "SEK.NZ", today=TODAY)["ran"] is True

    def test_gbp_and_gbp_pence_are_still_a_mismatch(self, repo):
        """Same money, different units -- a factor of 100, which is exactly
        the kind of error a currency check exists to catch."""
        make_db(repo, "WISE.L")
        make_prices(repo, "WISE.L")
        make_dcf(repo, "WISE.L", currency="GBP", quote_currency="GBp")
        with pytest.raises(sanity_check.DenominationError):
            sanity_check.check(repo, "WISE.L", today=TODAY)


class TestBlockShape:
    """8c.7 -- the block the orchestrator used to hand-write."""

    def block(self, repo, **kw):
        make_db(repo, "SEK.NZ")
        make_prices(repo, "SEK.NZ")
        make_dcf(repo, "SEK.NZ", **kw)
        return sanity_check.check(repo, "SEK.NZ", today=TODAY)

    def test_the_keys_are_the_spec_keys(self, repo):
        block = self.block(repo)
        for key in ("ran", "passed", "implied_multiples",
                    "historical_averages_ex_outliers", "trip_reasons",
                    "diagnosis", "historical_table", "computed_by",
                    "computed_at"):
            assert key in block, key

    def test_historical_averages_carry_the_maxima_the_rules_use(self, repo):
        avg = self.block(repo)["historical_averages_ex_outliers"]
        assert "pb_max" in avg
        assert "ev_ebitda_max" in avg

    def test_the_block_says_which_rules_could_be_evaluated(self, repo):
        """Measured on the corpus: 65 of 127 owner-FCF tickers have neither
        an implied P/E nor an implied EV/EBITDA, because 51 of them have no
        stock_based_comp in the latest FY. That is the SBC rule working --
        never mix adjusted and unadjusted -- but it means those tickers
        passed on P/B and P/Sales alone.

        A `passed: true` that was reached on two multiples must not read
        like one reached on four, so the block records which rules could be
        evaluated and which could not.
        """
        block = self.block(repo)
        assert set(block["rules_evaluated"]) == {"pe", "pb", "ev_ebitda",
                                                 "p_sales"}
        assert block["rules_not_evaluated"] == []

    def test_a_missing_sbc_is_named_as_the_reason_a_rule_was_skipped(self, repo):
        make_db(repo, "SEK.NZ", rows=[
            ("FY2024", 600.0, 120.0, 60.0, 480.0, 100.0, 20.0, 100.0, None),
            ("FY2025", 650.0, 130.0, 65.0, 520.0, 100.0, 20.0, 100.0, None)])
        make_prices(repo, "SEK.NZ")
        make_dcf(repo, "SEK.NZ")
        block = sanity_check.check(repo, "SEK.NZ", today=TODAY)
        assert set(block["rules_not_evaluated"]) == {"pe", "ev_ebitda"}
        assert block["rules_evaluated"] == ["pb", "p_sales"]
        # It still passes -- P/B and P/Sales are real evidence -- but the
        # reader can see the verdict rests on half the rules.
        assert block["passed"] is True

    def test_computed_by_names_the_script(self, repo):
        assert self.block(repo)["computed_by"] == "scripts/sanity_check.py"

    def test_a_clean_model_passes(self, repo):
        assert self.block(repo, )["passed"] is True

    def test_a_seka_shaped_model_fails_with_reasons(self, repo):
        block = self.block(repo, intrinsic_value=40.0)
        assert block["passed"] is False
        assert block["trip_reasons"]
        assert block["diagnosis"]

    def test_diagnosis_is_empty_when_nothing_tripped(self, repo):
        assert self.block(repo)["diagnosis"] == []

    def test_the_note_says_these_are_not_screener_multiples(self, repo):
        """They are SBC-adjusted on both sides, so they deliberately will
        not match Yahoo's GAAP P/E -- said here so a future reader does not
        'correct' it back."""
        assert "SBC" in self.block(repo)["basis"]


class TestApplyIsSurgical:
    """The DCF JSON is a reviewed artifact. json.dumps(indent=2) re-flows
    every inline array in the file and buries the one real change in a
    240-line diff."""

    def setup_ticker(self, repo, existing=None):
        """A DCF whose arrays are written INLINE, as the corpus has them.

        Starting from a json.dumps(indent=2) file would prove nothing: the
        array would already be exploded before apply() ran.
        """
        make_db(repo, "SEK.NZ")
        make_prices(repo, "SEK.NZ")
        path = make_dcf(repo, "SEK.NZ")
        doc = json.loads(path.read_text())
        text = json.dumps(doc, indent=2)
        insert = '  "historical_growth": {"fcf_growth_rates": [22.0, 20.0, 17.0]},\n'
        if existing is not None:
            insert += '  "sanity_check": ' + json.dumps(existing) + ",\n"
        text = text[:text.index("\n") + 1] + insert + text[text.index("\n") + 1:]
        path.write_text(text)
        json.loads(text)                       # the fixture must be valid
        return path

    def test_only_the_sanity_check_key_changes(self, repo):
        path = self.setup_ticker(repo, existing={"ran": True, "passed": True})
        before = path.read_text()
        sanity_check.apply(repo, "SEK.NZ", today=TODAY)
        after = path.read_text()
        assert before != after
        # Every other top-level value survives, and the untouched bytes are
        # literally untouched.
        b, a = json.loads(before), json.loads(after)
        b.pop("sanity_check"), a.pop("sanity_check")
        assert b == a
        assert '"fcf_growth_rates": [22.0, 20.0, 17.0]' in after

    def test_inline_arrays_are_not_reflowed(self, repo):
        path = self.setup_ticker(repo, existing={"ran": True, "passed": True})
        sanity_check.apply(repo, "SEK.NZ", today=TODAY)
        assert '"fcf_growth_rates": [22.0, 20.0, 17.0]' in path.read_text()

    def test_a_dcf_with_no_sanity_check_gains_one(self, repo):
        path = self.setup_ticker(repo)
        sanity_check.apply(repo, "SEK.NZ", today=TODAY)
        doc = json.loads(path.read_text())
        assert doc["sanity_check"]["computed_by"] == "scripts/sanity_check.py"

    def test_an_existing_fix_applied_is_preserved(self, repo):
        """8c.5 is the dcf-analyst's work. Overwriting its record of what
        it changed would make the fix unauditable on the next run."""
        path = self.setup_ticker(repo, existing={
            "ran": True, "passed": False,
            "fix_applied": "Switched to mid-cycle FCF $50m",
            "implied_multiples_after_fix": {"pe": 10.4, "pb": 1.17}})
        sanity_check.apply(repo, "SEK.NZ", today=TODAY)
        block = json.loads(path.read_text())["sanity_check"]
        assert block["fix_applied"] == "Switched to mid-cycle FCF $50m"
        assert block["implied_multiples_after_fix"]["pe"] == 10.4
        assert block["computed_by"] == "scripts/sanity_check.py"

    def test_the_result_is_still_valid_json(self, repo):
        path = self.setup_ticker(repo, existing={"ran": True, "passed": True})
        sanity_check.apply(repo, "SEK.NZ", today=TODAY)
        json.loads(path.read_text())

    def test_a_file_that_ends_without_a_newline_survives(self, repo):
        path = self.setup_ticker(repo)
        path.write_text(path.read_text().rstrip())
        sanity_check.apply(repo, "SEK.NZ", today=TODAY)
        json.loads(path.read_text())


class TestCli:
    def setup_ticker(self, repo, **kw):
        make_db(repo, "SEK.NZ")
        make_prices(repo, "SEK.NZ")
        return make_dcf(repo, "SEK.NZ", **kw)

    def test_a_pass_prints_the_block_and_exits_zero(self, repo, capsys):
        self.setup_ticker(repo)
        assert sanity_check.main(["SEK.NZ", "--as-of", TODAY.isoformat()]) == 0
        block = json.loads(capsys.readouterr().out)
        assert block["passed"] is True

    def test_a_trip_exits_three(self, repo, capsys):
        """Exit 3 is the orchestrator's signal to spawn the dcf-analyst
        once, with the printed trip reasons."""
        self.setup_ticker(repo, intrinsic_value=40.0)
        assert sanity_check.main(["SEK.NZ", "--as-of", TODAY.isoformat()]) == 3
        assert json.loads(capsys.readouterr().out)["trip_reasons"]

    def test_a_replaced_model_exits_zero(self, repo, capsys):
        path = self.setup_ticker(repo)
        doc = json.loads(path.read_text())
        doc["valuation_model"] = "NAV + exit discount"
        path.write_text(json.dumps(doc, indent=2))
        assert sanity_check.main(["SEK.NZ", "--as-of", TODAY.isoformat()]) == 0

    def test_without_apply_nothing_is_written(self, repo, capsys):
        path = self.setup_ticker(repo)
        before = path.read_text()
        sanity_check.main(["SEK.NZ", "--as-of", TODAY.isoformat()])
        assert path.read_text() == before

    def test_apply_writes_the_block(self, repo, capsys):
        path = self.setup_ticker(repo)
        sanity_check.main(["SEK.NZ", "--apply", "--as-of", TODAY.isoformat()])
        assert json.loads(path.read_text())["sanity_check"]["ran"] is True

    def test_a_missing_dcf_exits_two(self, repo, capsys):
        make_db(repo, "SEK.NZ")
        assert sanity_check.main(["SEK.NZ", "--as-of", TODAY.isoformat()]) == 2
        assert capsys.readouterr().err

    def test_all_dry_prints_a_table_and_writes_nothing(self, repo, capsys):
        path = self.setup_ticker(repo)
        before = path.read_text()
        assert sanity_check.main(["--all", "--dry", "--as-of", TODAY.isoformat()]) == 0
        out = capsys.readouterr().out
        assert "SEK.NZ" in out
        assert "stored" in out
        assert path.read_text() == before

    def test_all_dry_reports_the_disagreement(self, repo, capsys):
        path = self.setup_ticker(repo, intrinsic_value=40.0)
        doc = json.loads(path.read_text())
        doc["sanity_check"] = {"ran": True, "passed": True}
        path.write_text(json.dumps(doc, indent=2))
        sanity_check.main(["--all", "--dry", "--as-of", TODAY.isoformat()])
        out = capsys.readouterr().out
        assert "DIFF" in out

    def test_a_bad_as_of_exits_two(self, repo, capsys):
        self.setup_ticker(repo)
        assert sanity_check.main(["SEK.NZ", "--as-of", "last Tuesday"]) == 2
        assert "as-of" in capsys.readouterr().err

    def test_no_arguments_exits_two(self, repo, capsys):
        assert sanity_check.main([]) == 2


class TestNoRuleHadInputs:
    def test_null_units_leave_no_rule_evaluable_and_no_verdict(self, repo):
        """SDL.NZ (2026-09-08): core_metrics.units was NULL on every row, so
        metrics_normalized returned NULL for every money column, no implied
        multiple could be computed, and the block said `passed: true` on
        zero rules. A verdict reached on nothing is not a verdict."""
        make_db(repo, "SEK.NZ", units=None)
        make_prices(repo, "SEK.NZ")
        make_dcf(repo, "SEK.NZ")
        block = sanity_check.check(repo, "SEK.NZ", today=TODAY)
        assert block["rules_evaluated"] == []
        assert block["passed"] is None
        assert "not_evaluated_reason" in block
        assert "units" in block["not_evaluated_reason"]
