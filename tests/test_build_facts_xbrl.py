"""Tests for build_facts_xbrl.py: the SEC XBRL path for US filers.

What matters here and is pinned:

- period_label derives the period from the fact's OWN start/end dates, never
  its `fy` (a 10-K restates prior years, so `fy` is the filing's year).
- collect merges every listed concept (filers switch tags over time -- taking
  the first concept with any data would drop half the history) and ranks
  competitors within a period: 10-K beats 10-Q, later filing beats earlier
  (this is what keeps EPS history split-adjusted), concept order breaks ties.
- main scales absolute XBRL dollars to millions EXCEPT scale-free columns
  (eps, dividend_per_share, margins, period/units/currency). Skipping the
  scaling once left export_csv writing 45183036000 into a CSV whose dashboard
  embeds 45183.04 -- a silent 1e6 break.

All SEC traffic is stubbed: fixtures under tests/fixtures/xbrl/ are trimmed,
hand-built companyfacts-shaped JSON, copied into a tmp cache dir under the
exact filenames the module's cache lookup expects.
"""

import json
import shutil
import sys
from pathlib import Path

import build_facts_xbrl as bfx
import duckdb
import pytest

XBRL_FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to hit the SEC in a test is a bug in the test."""

    def _fail(url, dest=None):
        raise AssertionError(f"network fetch attempted: {url}")

    monkeypatch.setattr(bfx, "fetch", _fail)


@pytest.fixture
def xbrl_repo(tmp_path, monkeypatch, no_network):
    """A tmp repo with a warmed SEC cache built from the committed fixtures.

    cik_for and main read the cache files by exact name (company_tickers.json,
    CIK{cik:010d}.json), so copying the fixtures under those names means no
    fetch is ever attempted.
    """
    cache = tmp_path / "state" / "xbrl_cache"
    cache.mkdir(parents=True)
    shutil.copy(XBRL_FIXTURES / "company_tickers_trimmed.json",
                cache / "company_tickers.json")
    shutil.copy(XBRL_FIXTURES / "companyfacts_trimmed.json",
                cache / "CIK0001633917.json")
    (tmp_path / "research").mkdir()
    monkeypatch.setattr(bfx, "REPO", tmp_path)
    monkeypatch.setattr(bfx, "CACHE", cache)
    return tmp_path


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["build_facts_xbrl.py", *argv])
    return bfx.main()


def companyfacts():
    return json.loads((XBRL_FIXTURES / "companyfacts_trimmed.json").read_text())


class TestPeriodLabel:
    def test_annual_duration(self):
        assert bfx.period_label(
            {"start": "2024-01-01", "end": "2024-12-31"}) == "FY2024"

    def test_instant_is_fiscal_year(self):
        # Balance-sheet facts carry only an end date.
        assert bfx.period_label({"end": "2024-12-31"}) == "FY2024"

    def test_instant_off_the_fiscal_year_end_is_a_quarter(self):
        # An instant is a balance sheet AT a date. Labeling every one of them
        # FY{year} put Reddit's 30-Jun-2026 balance sheet in a phantom FY2026
        # row that had no revenue, while the real Q2 2026 row had revenue and
        # no balance sheet. Only the year-end instant is the annual one.
        assert bfx.period_label({"end": "2026-06-30"}, fy_end_month=12) \
            == "Q2 2026"
        assert bfx.period_label({"end": "2026-03-31"}, fy_end_month=12) \
            == "Q1 2026"
        assert bfx.period_label({"end": "2026-12-31"}, fy_end_month=12) \
            == "FY2026"

    def test_instant_honors_a_non_december_fiscal_year_end(self):
        # Visa closes in September: the Sept instant is the year-end, and the
        # December one is a quarter, the opposite of a calendar-year filer.
        assert bfx.period_label({"end": "2025-09-30"}, fy_end_month=9) \
            == "FY2025"
        assert bfx.period_label({"end": "2025-12-31"}, fy_end_month=9) \
            == "Q4 2025"

    def test_instant_without_a_known_fiscal_year_end_stays_annual(self):
        # No fiscal-year context (the default) keeps the old behaviour, so
        # callers that cannot derive it are not silently changed.
        assert bfx.period_label({"end": "2024-06-30"}) == "FY2024"

    def test_fy_field_is_ignored(self):
        # A 10-K restates prior years, so `fy` is the filing's year, not the
        # fact's. The dates are authoritative.
        assert bfx.period_label(
            {"start": "2023-01-01", "end": "2023-12-31",
             "fy": 2025, "fp": "Q1"}) == "FY2023"

    def test_cross_calendar_fiscal_year_labeled_by_end_year(self):
        # A Jan/Feb year-end (LULU-style) labels by the calendar year the
        # period ENDS in, whatever the company's own labeling convention.
        assert bfx.period_label(
            {"start": "2024-02-04", "end": "2025-02-01"}) == "FY2025"

    def test_half_years(self):
        assert bfx.period_label(
            {"start": "2024-01-01", "end": "2024-06-30"}) == "H1-2024"
        assert bfx.period_label(
            {"start": "2024-07-01", "end": "2024-12-31"}) == "H2-2024"

    def test_quarter_uses_space_separator(self):
        # "Q1 2024", not "Q1-2024" -- export_csv.sort_key splits on both, so
        # the space form sorts correctly, but the shape is pinned here.
        assert bfx.period_label(
            {"start": "2024-01-01", "end": "2024-03-31"}) == "Q1 2024"

    def test_quarter_is_calendar_quarter_of_end_month(self):
        # A Feb-Apr fiscal quarter maps to calendar Q2 because only the end
        # month is consulted.
        assert bfx.period_label(
            {"start": "2024-02-01", "end": "2024-04-30"}) == "Q2 2024"

    def test_nine_month_ytd_gets_a_label_that_cannot_collide(self):
        # A 273-day 10-Q YTD span is neither a half nor a quarter. Labeling
        # it H2 let it collide with -- and, filed later, overwrite -- a
        # genuine second half, so it must never take a real period's name.
        #
        # It is kept rather than dropped because it is the only place Q3
        # cash flow exists (a 10-Q reports cash flow year to date), and
        # decumulate() subtracts H1 from it. drop_scaffolding() removes it
        # before the write, so it never reaches core_metrics.
        assert bfx.period_label(
            {"start": "2024-01-01", "end": "2024-09-30"}) == "9M-2024"
        # A real second half (~184 days) still labels as H2, unaffected.
        assert bfx.period_label(
            {"start": "2024-07-01", "end": "2024-12-31"}) == "H2-2024"

    def test_the_scaffolding_label_is_not_a_real_period(self):
        """periods.py must not parse 9M-YYYY as a reporting period."""
        import periods
        assert not periods.is_annual(periods.parse("9M-2024"))

    def test_missing_end_is_none(self):
        assert bfx.period_label({}) is None
        assert bfx.period_label({"end": ""}) is None

    def test_52_53_week_year_end_drifting_past_the_month_boundary(self):
        """Adobe closes on the Friday nearest Nov 30, so some years end in Dec.

        `month == fy_end_month` is exact, so FY2021 (ending 2021-12-03) and
        FY2022 (2022-12-02) were labeled Q4 -- and the annual balance sheet
        for those years simply did not exist. ADBE lost equity, cash and
        debt for 4 of 19 years that way. A 52/53-week calendar is normal for
        US filers (Apple, Cisco, Lululemon all use one); the year end drifts
        a few days either side of a fixed month, and an instant landing in
        that window is the annual balance sheet.
        """
        # Nov-30 filer whose FY2021 actually closed 2021-12-03.
        assert bfx.period_label({"end": "2021-12-03"}, fy_end_month=11) == "FY2021"
        assert bfx.period_label({"end": "2022-12-02"}, fy_end_month=11) == "FY2022"
        # And the ordinary in-month year ends still work.
        assert bfx.period_label({"end": "2025-11-28"}, fy_end_month=11) == "FY2025"

    def test_drift_window_does_not_swallow_a_real_quarter(self):
        """Tolerance is days, not a month: Q1 must stay Q1.

        A Nov year-end filer's first quarter closes end-Feb/early-Mar. If
        the window were wide enough to catch that, every filer would grow a
        second phantom FY row.
        """
        assert bfx.period_label({"end": "2022-03-04"}, fy_end_month=11) == "Q1 2022"
        assert bfx.period_label({"end": "2022-06-03"}, fy_end_month=11) == "Q2 2022"
        assert bfx.period_label({"end": "2022-09-02"}, fy_end_month=11) == "Q3 2022"

    def test_drift_backwards_into_the_previous_month(self):
        """The mirror of the ADBE case: the year end drifts a few days EARLY.

        Cisco closes on the last Saturday of July, which in some years is
        the 25th or 26th; a filer whose modal year-end month is August can
        therefore close in late July. The window is symmetric, so an
        instant just before the boundary is annual too -- while a date a
        full quarter away stays a quarter.
        """
        assert bfx.period_label({"end": "2024-07-27"}, fy_end_month=7) == "FY2024"
        assert bfx.period_label({"end": "2024-08-03"}, fy_end_month=7) == "FY2024"
        # A genuine quarter end, a month clear of the boundary, is unaffected.
        assert bfx.period_label({"end": "2024-10-31"}, fy_end_month=7) == "Q4 2024"


class TestFiscalYearEndMonth:
    """The FY-end month comes from the filer's own annual durations.

    Nothing in a bare instant fact says which month closes the year, so it is
    read off the ~365-day duration facts, where the end month IS the year end.
    """

    def test_derived_from_annual_durations(self):
        facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"start": "2023-01-01", "end": "2023-12-31", "val": 1},
            {"start": "2024-01-01", "end": "2024-12-31", "val": 2},
            {"start": "2024-01-01", "end": "2024-03-31", "val": 3},
        ]}}}}}
        assert bfx.fiscal_year_end_month(facts) == 12

    def test_september_filer(self):
        facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"start": "2023-10-01", "end": "2024-09-30", "val": 1},
            {"start": "2024-10-01", "end": "2025-09-30", "val": 2},
        ]}}}}}
        assert bfx.fiscal_year_end_month(facts) == 9

    def test_no_annual_durations_yields_none(self):
        facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"start": "2024-01-01", "end": "2024-03-31", "val": 1},
        ]}}}}}
        assert bfx.fiscal_year_end_month(facts) is None

    def test_empty_facts_do_not_raise(self):
        assert bfx.fiscal_year_end_month({}) is None


class TestCikFor:
    def test_dotted_ticker_is_not_us(self, xbrl_repo):
        # NZX/LSE/HKEX suffixes never resolve; those go through build_facts.py.
        assert bfx.cik_for("SEK.NZ") is None

    def test_resolves_from_cache_case_insensitively(self, xbrl_repo):
        assert bfx.cik_for("TRIM") == 1633917
        assert bfx.cik_for("trim") == 1633917

    def test_unknown_ticker_is_none(self, xbrl_repo):
        assert bfx.cik_for("ZZZZ") is None

    def test_fetch_failure_is_none_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bfx, "CACHE", tmp_path / "empty_cache")

        def _boom(url, dest=None):
            raise OSError("offline")

        monkeypatch.setattr(bfx, "fetch", _boom)
        assert bfx.cik_for("TRIM") is None


class TestCollect:
    def test_merges_history_across_concepts(self):
        # PayPal case: FY2022 exists only under the old tag, FY2024 only
        # under the new one. First-concept-with-any-data would drop years.
        _, values, unit = bfx.collect(
            companyfacts(), bfx.CONCEPTS["revenue"])
        assert set(values) == {"FY2022", "FY2023", "FY2024", "Q1 2024"}
        assert values["FY2022"] == 20000000000
        assert values["FY2024"] == 40000000000
        assert unit == "USD"

    def test_used_label_joins_contributing_concepts_in_preference_order(self):
        concept, _, _ = bfx.collect(companyfacts(), bfx.CONCEPTS["revenue"])
        assert concept == ("RevenueFromContractWithCustomerExcludingAssessedTax"
                          "+Revenues")

    def test_later_filing_beats_concept_preference(self):
        # FY2023 revenue: 29bn under the preferred concept (filed 2023),
        # 30bn under the second (filed 2024). The later filing wins.
        _, values, _ = bfx.collect(companyfacts(), bfx.CONCEPTS["revenue"])
        assert values["FY2023"] == 30000000000

    def test_later_filing_keeps_eps_split_adjusted(self):
        # The Netflix 10-for-1 case: FY2023 EPS is 19.83 in the 2024 10-K and
        # 1.98 in the 2025 one. Taking the newer value keeps the series on
        # today's share basis; disagreeing with contemporaneous headlines is
        # expected, not a bug.
        _, values, _ = bfx.collect(companyfacts(), bfx.CONCEPTS["eps"])
        assert values["FY2023"] == 1.98

    def test_10k_beats_10q_regardless_of_filing_date(self):
        # FY2023 net income appears in the 2024 10-K (5bn) and in a 10-Q
        # comparative filed a year LATER (5.555bn). Form rank dominates.
        _, values, _ = bfx.collect(companyfacts(), bfx.CONCEPTS["net_income"])
        assert values["FY2023"] == 5000000000

    def test_concept_order_breaks_exact_ties(self):
        facts = {"facts": {"us-gaap": {
            "First": {"units": {"USD": [
                {"start": "2024-01-01", "end": "2024-12-31", "val": 1,
                 "form": "10-K", "filed": "2025-02-01"}]}},
            "Second": {"units": {"USD": [
                {"start": "2024-01-01", "end": "2024-12-31", "val": 2,
                 "form": "10-K", "filed": "2025-02-01"}]}},
        }}}
        concept, values, _ = bfx.collect(facts, ["First", "Second"])
        assert values["FY2024"] == 1
        # A concept whose every row lost is not credited in `used`.
        assert concept == "First"

    def test_no_data_returns_none_and_usd_default(self):
        assert bfx.collect({}, ["Revenues"]) == (None, {}, "USD")
        assert bfx.collect(
            {"facts": {"us-gaap": {}}}, ["GrossProfit"]) == (None, {}, "USD")

    def test_facts_without_a_period_are_skipped(self):
        facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"val": 5, "form": "10-K", "filed": "2025-02-01"},
            {"start": "2024-01-01", "end": "2024-12-31", "val": 7,
             "form": "10-K", "filed": "2025-02-01"},
        ]}}}}}
        _, values, _ = bfx.collect(facts, ["Revenues"])
        assert values == {"FY2024": 7}


def db_row(repo, period):
    con = duckdb.connect(
        str(repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
        read_only=True)
    row = con.execute(
        "SELECT revenue, net_income, eps, dividend_per_share, total_assets,"
        "       cost_of_revenue, units, currency"
        " FROM core_metrics WHERE period = ?", [period]).fetchone()
    con.close()
    return row


class TestDerivedColumns:
    """FCF and the margin columns are computed, never tagged.

    The XBRL path only ever took directly-tagged concepts, so a rebuilt
    ticker landed with free_cash_flow 5/109 while the CSV it replaced had
    it everywhere -- export_csv.py then refused the export rather than
    blank 488 populated cells. The agent-adjudicated path has always
    derived these; XBRL has to as well or the two paths disagree.
    """

    def test_fcf_derived_from_ocf_minus_capex(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        ocf, capex, fcf = con.execute(
            "SELECT operating_cash_flow, capex, free_cash_flow"
            " FROM core_metrics WHERE period = 'FY2024'").fetchone()
        con.close()
        assert (ocf, capex) == (9000.0, 600.0)
        assert fcf == 8400.0

    def test_margins_derived_as_percentages(self, xbrl_repo, monkeypatch):
        # Revenue 40bn, gross profit 24bn, net income 6bn.
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        gm, nm = con.execute(
            "SELECT gross_margin, net_margin"
            " FROM core_metrics WHERE period = 'FY2024'").fetchone()
        con.close()
        assert gm == pytest.approx(60.0)
        assert nm == pytest.approx(15.0)

    def test_derived_columns_absent_when_inputs_missing(self, xbrl_repo,
                                                       monkeypatch):
        """FY2023 has revenue and net income but no OCF/capex/gross profit:
        the margin it can compute is filled, the rest stay NULL rather than
        becoming a guessed zero."""
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        fcf, gm, nm = con.execute(
            "SELECT free_cash_flow, gross_margin, net_margin"
            " FROM core_metrics WHERE period = 'FY2023'").fetchone()
        con.close()
        assert fcf is None
        assert gm is None
        assert nm is not None


class TestMainWritesDb:
    def test_money_columns_scaled_to_millions(self, xbrl_repo, monkeypatch):
        # XBRL reports absolute dollars; core_metrics' convention is
        # millions. Unscaled, export_csv once wrote 45183036000 into a CSV
        # whose dashboard embedded 45183.04 -- silently broken by 1e6.
        assert run_main(monkeypatch, "TRIM") == 0
        revenue, net_income, *_ = db_row(xbrl_repo, "FY2024")
        assert revenue == 40000.0
        assert net_income == 6000.0

    def test_scale_free_columns_untouched(self, xbrl_repo, monkeypatch):
        # EPS of 2.30 is 2.30 whatever the revenue units are.
        assert run_main(monkeypatch, "TRIM") == 0
        _, _, eps, dps, *_ = db_row(xbrl_repo, "FY2024")
        assert eps == 2.3
        assert dps == 0.5

    def test_units_and_currency_stamped(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        *_, units, currency = db_row(xbrl_repo, "FY2024")
        assert units == "millions"
        assert currency == "USD"

    def test_instant_facts_land_on_their_fiscal_year(self, xbrl_repo,
                                                     monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        assert db_row(xbrl_repo, "FY2024")[4] == 100000.0
        assert db_row(xbrl_repo, "FY2023")[4] == 90000.0

    def test_untagged_concept_stays_null(self, xbrl_repo, monkeypatch):
        # Deliberately NO model fallback: absent from XBRL means genuinely
        # untagged. Missing stays NULL. (cost_of_revenue is the untagged
        # example here -- gross_profit IS tagged in the fixture, because
        # TestDerivedColumns needs it to compute a gross margin.)
        assert run_main(monkeypatch, "TRIM") == 0
        assert db_row(xbrl_repo, "FY2024")[5] is None

    def test_quarterly_period_gets_its_own_row(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        assert db_row(xbrl_repo, "Q1 2024")[0] == 9000.0

    def test_underscore_concepts_land_in_kpis_scaled(self, xbrl_repo,
                                                     monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        kpis = con.execute(
            "SELECT period, name, value, unit FROM kpis").fetchall()
        con.close()
        assert kpis == [("FY2024", "ebitda_da", 1500.0, "millions")]

    def test_rerun_replaces_rather_than_duplicates(self, xbrl_repo,
                                                   monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        n_core = con.execute("SELECT count(*) FROM core_metrics").fetchone()[0]
        n_kpis = con.execute("SELECT count(*) FROM kpis").fetchone()[0]
        con.close()
        # FY2022-24, H1-2024, Q1 2024, plus Q2/Q3 2024 decumulated from the
        # cumulative cash-flow spans. The 9M-2024 scaffolding span that made
        # Q3 derivable is dropped rather than written.
        assert n_core == 7
        assert n_kpis == 1

    def test_check_mode_writes_nothing(self, xbrl_repo, monkeypatch, capsys):
        assert run_main(monkeypatch, "TRIM", "--check") == 0
        assert not (xbrl_repo / "research" / "TRIM").exists()
        out = capsys.readouterr().out
        assert "Trimmed Example Co" in out
        assert "cost_of_revenue" in out  # missing concepts are reported

    def test_show_reports_concept_provenance(self, xbrl_repo, monkeypatch,
                                             capsys):
        assert run_main(monkeypatch, "TRIM", "--show") == 0
        out = capsys.readouterr().out
        assert "eps" in out
        assert "EarningsPerShareDiluted" in out
        assert "NOT TAGGED (left NULL)" in out

    def test_non_us_ticker_exits_2(self, xbrl_repo, monkeypatch, capsys):
        assert run_main(monkeypatch, "SEK.NZ") == 2
        assert "use build_facts.py" in capsys.readouterr().err
        assert not (xbrl_repo / "research" / "SEK.NZ").exists()

    def test_unknown_ticker_exits_2(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "ZZZZ") == 2

    def test_companyfacts_fetch_failure_exits_1(self, xbrl_repo, monkeypatch,
                                                capsys):
        # AAPL is in the ticker map but has no cached companyfacts, so the
        # (stubbed, failing) fetch path is taken.
        def _boom(url, dest=None):
            raise OSError("offline")

        monkeypatch.setattr(bfx, "fetch", _boom)
        assert run_main(monkeypatch, "AAPL") == 1
        assert "SEC fetch failed" in capsys.readouterr().err


class TestTotalDebt:
    """total_debt is a SUM of two tags, and was absent from CONCEPTS entirely.

    Every other core column is a preference pick: the first listed concept
    with data for a period wins. Debt is not -- a filer reports the current
    portion and the non-current portion under separate tags, and the DCF
    wants the total. ADBE tags LongTermDebt 4.80B and DebtCurrent 1.84B at
    2026-05-29; the answer is 6.64B, which is neither of them.

    Left unmapped, the XBRL path produced total_debt 0/109 for a
    DCF-essential field, and export_csv.py refused the export rather than
    blank the column the previous CSV had populated.
    """

    def test_total_debt_is_the_sum_of_the_components(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        got = con.execute(
            "SELECT total_debt FROM core_metrics WHERE period = 'FY2024'").fetchone()[0]
        assert got == pytest.approx(7000.0)  # 5000 + 2000, in millions

    def test_sums_for_every_period_not_just_the_latest(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        got = con.execute(
            "SELECT total_debt FROM core_metrics WHERE period = 'FY2023'").fetchone()[0]
        assert got == pytest.approx(5000.0)  # 4000 + 1000

    def test_a_debt_free_filer_gets_null_not_zero(self, xbrl_repo, monkeypatch):
        """No debt tags at all means unknown, not zero.

        The units rule applies to concepts too: a missing row is obvious,
        a plausible wrong one is not. A filer that tags neither component
        must leave the column NULL so the gap is visible.
        """
        facts = companyfacts()
        del facts["facts"]["us-gaap"]["LongTermDebt"]
        del facts["facts"]["us-gaap"]["DebtCurrent"]
        _, values, _ = bfx.collect_sum(facts, bfx.SUM_CONCEPTS["total_debt"])
        assert values == {}

    def test_a_period_with_only_one_component_uses_that_component(self):
        """A filer with no current portion still has debt."""
        facts = companyfacts()
        del facts["facts"]["us-gaap"]["DebtCurrent"]
        _, values, _ = bfx.collect_sum(facts, bfx.SUM_CONCEPTS["total_debt"])
        assert values["FY2024"] == 5_000_000_000


class TestCumulativeCashFlowIsDecumulated:
    """A 10-Q reports cash flow year-to-date, so Q2 and Q3 are never tagged.

    The income statement in a 10-Q covers the quarter, but the cash flow
    statement covers the year to date -- Q2's span is six months and Q3's
    is nine. So SEC has no fact for "cash from operations in Q2", and the
    XBRL path left operating_cash_flow, capex and free_cash_flow empty for
    every Q2 and Q3 in a filer's history (ADBE: 54 cells the CSV it
    replaced had populated, which export_csv.py then refused to blank).

    The values are all present, just cumulatively: Q2 = H1 - Q1 and
    Q3 = 9-month - H1. Deriving them is the same contract as derive() --
    XBRL must compute what SEC never tags, or the two extraction paths
    produce different schemas for the same company.
    """

    def test_q2_is_the_half_year_less_q1(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        got = con.execute("SELECT operating_cash_flow FROM core_metrics"
                          " WHERE period = 'Q2 2024'").fetchone()[0]
        assert got == pytest.approx(2500.0)  # 4500 - 2000

    def test_q3_is_the_nine_month_ytd_less_the_half_year(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        got = con.execute("SELECT operating_cash_flow FROM core_metrics"
                          " WHERE period = 'Q3 2024'").fetchone()[0]
        assert got == pytest.approx(2300.0)  # 6800 - 4500

    def test_capex_decumulates_too(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        assert con.execute("SELECT capex FROM core_metrics"
                           " WHERE period = 'Q2 2024'").fetchone()[0] \
            == pytest.approx(150.0)   # 250 - 100
        assert con.execute("SELECT capex FROM core_metrics"
                           " WHERE period = 'Q3 2024'").fetchone()[0] \
            == pytest.approx(150.0)   # 400 - 250

    def test_derived_quarters_get_free_cash_flow(self, xbrl_repo, monkeypatch):
        """FCF must follow from the decumulated parts, not stay NULL."""
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        got = con.execute("SELECT free_cash_flow FROM core_metrics"
                          " WHERE period = 'Q2 2024'").fetchone()[0]
        assert got == pytest.approx(2350.0)  # 2500 - 150

    def test_a_directly_tagged_quarter_is_not_overwritten(self):
        """Some filers do tag the discrete quarter. Never clobber a real fact."""
        rows = {
            "Q1 2024": {"operating_cash_flow": 2000.0},
            "H1-2024": {"operating_cash_flow": 4500.0},
            "Q2 2024": {"operating_cash_flow": 9999.0},
        }
        bfx.decumulate(rows)
        assert rows["Q2 2024"]["operating_cash_flow"] == 9999.0

    def test_a_missing_predecessor_leaves_the_quarter_null(self):
        """No Q1 means Q2 is unknown, not equal to the half year."""
        rows = {"H1-2024": {"operating_cash_flow": 4500.0}}
        bfx.decumulate(rows)
        assert rows.get("Q2 2024", {}).get("operating_cash_flow") is None


class TestEbitdaDerived:
    """EBITDA is a convention, not a tagged line item, and D&A lives in kpis.

    SEC tags DepreciationDepletionAndAmortization but never "EBITDA", so the
    column came back 0/106 on ADBE -- leaving the dashboard and the Step 8c
    EV/EBITDA sanity check with nothing to chart on a filer whose operating
    income and D&A were both present for 56 periods.

    Same contract as free_cash_flow and the margins: the text path computes
    it, so the XBRL path must too, or the two disagree for one company.
    """

    def test_ebitda_is_operating_income_plus_da(self):
        rec = {"operating_income": 8706.0}
        bfx.derive(rec, da=818.0)
        assert rec["ebitda"] == pytest.approx(9524.0)

    def test_missing_da_leaves_ebitda_null(self):
        """No D&A means unknown, not equal to operating income."""
        rec = {"operating_income": 8706.0}
        bfx.derive(rec, da=None)
        assert rec.get("ebitda") is None

    def test_missing_operating_income_leaves_ebitda_null(self):
        rec = {"operating_income": None}
        bfx.derive(rec, da=818.0)
        assert rec.get("ebitda") is None

    def test_a_tagged_ebitda_is_not_overwritten(self):
        rec = {"operating_income": 8706.0, "ebitda": 9999.0}
        bfx.derive(rec, da=818.0)
        assert rec["ebitda"] == 9999.0

    def test_da_is_optional_so_existing_callers_still_work(self):
        rec = {"operating_income": 100.0, "revenue": 1000.0}
        bfx.derive(rec)
        assert rec.get("ebitda") is None
        assert rec["operating_margin"] == pytest.approx(10.0)

    def test_main_populates_ebitda_from_the_kpi(self, xbrl_repo, monkeypatch):
        assert run_main(monkeypatch, "TRIM") == 0
        con = duckdb.connect(
            str(xbrl_repo / "research" / "TRIM" / "Reports" / "TRIM.duckdb"),
            read_only=True)
        oi, da, ebitda = con.execute(
            "SELECT c.operating_income, k.value, c.ebitda FROM core_metrics c"
            " LEFT JOIN kpis k ON k.period = c.period AND k.name = 'ebitda_da'"
            " WHERE c.period = 'FY2024'").fetchone()
        con.close()
        if oi is not None and da is not None:
            assert ebitda == pytest.approx(oi + da)
