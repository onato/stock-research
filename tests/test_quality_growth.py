"""The quality-growth list: accelerating revenue, high returns, low debt, IV upside.

Pure classification rules are tested directly; `build` runs against a tiny
in-memory warehouse with the same table shapes as state/research.duckdb, and
the ROIC lookup is injected so no test reads research/.
"""

import datetime as dt
from typing import Any

import duckdb
import pytest
import quality_growth as qg

# --- acceleration ---------------------------------------------------------

def test_three_rising_growth_rates_accelerate() -> None:
    acc = qg.acceleration({2022: 100, 2023: 110, 2024: 125, 2025: 150})
    assert acc is not None
    assert acc["fiscal_year"] == 2025
    assert [round(g, 3) for g in acc["growth"]] == [0.1, 0.136, 0.2]


def test_flat_or_falling_growth_does_not_accelerate() -> None:
    assert qg.acceleration({2022: 100, 2023: 120, 2024: 140, 2025: 160}) is None


def test_gap_year_breaks_the_series() -> None:
    assert qg.acceleration({2021: 100, 2023: 110, 2024: 125, 2025: 150}) is None


def test_growth_below_floor_is_not_listed() -> None:
    # 1% -> 3% -> 6% is accelerating but not growth worth listing.
    assert qg.acceleration({2022: 100, 2023: 101, 2024: 104.03, 2025: 110.27}) is None


def test_implausible_jump_is_rejected() -> None:
    # >150% in a year is a merger or a units error, not organic acceleration.
    assert qg.acceleration({2022: 100, 2023: 110, 2024: 125, 2025: 400}) is None


# --- interim confirmation -------------------------------------------------

@pytest.mark.parametrize(("fy", "interim", "expected"), [
    (0.20, 0.25, "confirming"),
    (0.20, 0.185, "confirming"),   # within 10% of the FY rate still holds
    (0.20, 0.12, "slowing"),
    (0.20, None, "unknown"),
])
def test_interim_trend(fy: float, interim: float | None, expected: str) -> None:
    assert qg.interim_trend(fy, interim) == expected


# --- classification -------------------------------------------------------

def _row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "growth": [0.10, 0.15, 0.20], "interim_trend": "confirming",
        "return_pct": 25.0, "financial": False, "debt": 10.0, "cash": 50.0,
        "equity": 100.0, "upside_pct": 20.0, "sector": "Software",
    }
    base.update(kw)
    return base


def test_all_tests_pass_is_qualified() -> None:
    assert qg.classify(_row()) == ("qualified", [])


def test_only_valuation_failing_is_expensive() -> None:
    assert qg.classify(_row(upside_pct=-15.0)) == ("expensive", ["valuation"])


def test_one_other_failure_with_upside_is_near_miss() -> None:
    assert qg.classify(_row(interim_trend="slowing")) == ("near_miss", ["slowing"])
    assert qg.classify(_row(return_pct=12.0)) == ("near_miss", ["returns"])


def test_one_failure_plus_valuation_is_still_a_near_miss() -> None:
    # A great business that is levered *and* expensive stays visible.
    assert qg.classify(_row(return_pct=12.0, upside_pct=-5.0)) == ("near_miss", ["returns", "valuation"])


def test_two_non_valuation_failures_drop_out() -> None:
    tier, failures = qg.classify(_row(return_pct=12.0, interim_trend="slowing"))
    assert tier is None
    assert failures == ["slowing", "returns"]


def test_missing_return_is_unknown_not_a_pass() -> None:
    assert qg.classify(_row(return_pct=None)) == ("incomplete", ["returns_unknown"])


def test_missing_iv_is_a_valuation_failure() -> None:
    assert qg.classify(_row(upside_pct=None))[1] == ["valuation"]


def test_debt_above_half_equity_fails_leverage() -> None:
    assert qg.classify(_row(debt=60.0, cash=0.0))[1] == ["leverage"]


def test_negative_equity_with_net_debt_fails_leverage() -> None:
    assert qg.classify(_row(equity=-50.0, debt=200.0, cash=20.0))[1] == ["leverage"]


def test_negative_equity_with_net_cash_passes_leverage() -> None:
    assert qg.classify(_row(equity=-50.0, debt=10.0, cash=20.0))[1] == []


def test_financials_skip_the_leverage_test() -> None:
    assert qg.classify(_row(financial=True, debt=900.0, cash=0.0))[1] == []


def test_missing_debt_is_unknown_never_zero() -> None:
    # A blank is not evidence of no debt; reading it as zero would let a
    # heavily indebted company through on a data gap.
    assert qg.classify(_row(debt=None, cash=None)) == ("incomplete", ["leverage_unknown"])


def test_missing_equity_is_unknown() -> None:
    assert qg.classify(_row(equity=None))[1] == ["leverage_unknown"]


def test_negative_equity_with_unknown_cash_is_unknown() -> None:
    assert qg.classify(_row(equity=-50.0, debt=10.0, cash=None))[1] == ["leverage_unknown"]


def test_unknowns_plus_two_known_failures_drop_out() -> None:
    tier, _ = qg.classify(_row(return_pct=None, interim_trend="slowing", debt=900.0, cash=0.0))
    assert tier is None


def test_recovery_from_decline_is_excluded() -> None:
    assert qg.exclusion(_row(growth=[-0.05, 0.09, 0.15])) == "recovery: FY growth was negative two years ago"


@pytest.mark.parametrize("sector", ["Gold Mining", "Copper Mining / Metals & Mining",
                                    "Oil & Gas Exploration & Production",
                                    "Basic Materials / Nitrogen Fertilizers"])
def test_commodity_sectors_are_excluded(sector: str) -> None:
    assert qg.exclusion(_row(sector=sector)) == f"commodity-cyclical sector: {sector}"


def test_ordinary_sector_is_not_excluded() -> None:
    assert qg.exclusion(_row(sector="Medical Devices / Robotic Surgery")) is None


# --- notes and review -----------------------------------------------------

TODAY = dt.date(2026, 10, 17)


def test_unreviewed_candidate_needs_review() -> None:
    assert qg.needs_review({"tier": "qualified"}, None, TODAY)


def test_tier_change_needs_review() -> None:
    note = {"reviewed_at": "2026-10-10", "tier_at_review": "expensive"}
    assert qg.needs_review({"tier": "qualified"}, note, TODAY)


def test_stale_note_needs_review() -> None:
    note = {"reviewed_at": "2026-08-01", "tier_at_review": "qualified"}
    assert qg.needs_review({"tier": "qualified"}, note, TODAY)


def test_fresh_matching_note_is_left_alone() -> None:
    note = {"reviewed_at": "2026-10-10", "tier_at_review": "qualified"}
    assert not qg.needs_review({"tier": "qualified"}, note, TODAY)


def test_notes_load_one_file_per_ticker(tmp_path: Any) -> None:
    (tmp_path / "RDDT.json").write_text('{"why": "w"}')
    assert qg.load_notes(tmp_path) == {"RDDT": {"why": "w"}}
    assert qg.load_notes(tmp_path / "missing") == {}


# --- build against a tiny warehouse ---------------------------------------

def _warehouse() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("""create table metrics_m (ticker varchar, period varchar, period_type varchar,
        fiscal_year integer, months integer, is_annual boolean, revenue double,
        total_debt double, cash_and_equivalents double, shareholders_equity double)""")
    con.execute("create table companies (ticker varchar, name varchar, sector varchar)")
    con.execute("""create table dcf (ticker varchar, valuation_date varchar, current_price double,
        weighted_iv double, sanity_passed boolean, quote_currency varchar)""")
    con.execute("create table dcf_scenarios (ticker varchar, scenario varchar, entry_price double)")
    con.execute("create table prices (ticker varchar, date date, close double)")

    def company(t: str, name: str, sector: str, revs: list[float], h1: tuple[float, float] | None,
                iv: float, price: float, valued: str = "2026-10-01", sane: bool = True) -> None:
        con.execute("insert into companies values (?, ?, ?)", [t, name, sector])
        for i, r in enumerate(revs):
            y = 2022 + i
            con.execute("insert into metrics_m values (?, ?, 'FY', ?, 12, true, ?, 10, 50, 100)",
                        [t, f"FY{y}", y, r])
        if h1:
            for y, r in zip((2025, 2026), h1, strict=True):
                con.execute("insert into metrics_m values (?, ?, 'H1', ?, 6, false, ?, 10, 50, 100)",
                            [t, f"H1 {y}", y, r])
        con.execute("insert into dcf values (?, ?, ?, ?, ?, 'USD')", [t, valued, price, iv, sane])
        con.execute("insert into dcf_scenarios values (?, 'base', ?)", [t, iv * 0.7])

    company("GROW", "Grow Inc", "Software", [100, 110, 125, 150], (70, 90), iv=120, price=100)
    company("PRICY", "Pricy Inc", "Software", [100, 110, 125, 150], (70, 90), iv=80, price=100)
    con.execute("insert into metrics_m values ('PRICY', 'Q3 2025', 'Q3', 2025, 3, false, 40, 10, 50, 100)")
    con.execute("insert into metrics_m values ('PRICY', 'Q3 2026', 'Q3', 2026, 3, false, 50, 10, 50, 100)")
    company("GOLD", "Gold Co", "Gold Mining", [100, 110, 125, 150], None, iv=200, price=100)
    company("FLAT", "Flat Co", "Software", [100, 120, 140, 160], None, iv=200, price=100)
    # Dual listing: same company twice; the fresher sane valuation wins.
    company("DUAL", "Dual Corp", "Software", [100, 110, 125, 150], (70, 90), iv=130, price=100,
            valued="2026-09-01")
    company("DUAL.HK", "Dual Corp", "Software", [100, 110, 125, 150], (70, 90), iv=140, price=100,
            valued="2026-10-05")
    # A newer quote than the DCF's own price is used for upside.
    con.execute("insert into prices values ('GROW', '2026-10-16', 96)")
    return con


def _returns(ticker: str) -> dict[str, Any]:
    return {"measure": "ROIC", "latest": 30.0, "avg3": 28.0, "financial": False}


def test_build_tiers_dedupes_and_excludes() -> None:
    out = qg.build(_warehouse(), _returns, notes={}, today=TODAY)
    by = {c["ticker"]: c for c in out["candidates"]}
    assert set(by) == {"GROW", "PRICY", "DUAL.HK"}
    assert by["GROW"]["tier"] == "qualified"
    assert by["PRICY"]["tier"] == "expensive"
    assert by["GROW"]["price"] == 96
    assert by["GROW"]["price_date"] == "2026-10-16"
    assert by["GROW"]["upside_pct"] == pytest.approx(25.0)
    assert by["GROW"]["interim"] == {"period": "H1 2026", "yoy_pct": pytest.approx(28.6, abs=0.1),
                                     "trend": "confirming"}
    assert by["GROW"]["entry_price"] == pytest.approx(84.0)
    # The latest-ending interim wins over a longer, older one.
    assert by["PRICY"]["interim"]["period"] == "Q3 2026"
    excluded = {e["ticker"]: e["reason"] for e in out["excluded"]}
    assert excluded["GOLD"].startswith("commodity-cyclical")
    assert "FLAT" not in excluded          # never accelerated, so never a candidate
    # Tier first, then upside: DUAL.HK (+40%) outranks GROW (+25%).
    assert out["needs_review"] == ["DUAL.HK", "GROW", "PRICY"]


def test_analyst_exclusion_moves_ticker_out_with_its_reason() -> None:
    notes = {"PRICY": {"exclude": True, "exclude_reason": "acquisition-driven",
                       "reviewed_at": "2026-10-16", "tier_at_review": "expensive"}}
    out = qg.build(_warehouse(), _returns, notes=notes, today=TODAY)
    assert "PRICY" not in {c["ticker"] for c in out["candidates"]}
    assert {"ticker": "PRICY", "reason": "analyst: acquisition-driven"} in out["excluded"]


def test_notes_ride_along_on_candidates() -> None:
    notes = {"GROW": {"why": "w", "watch": ["a"], "reviewed_at": "2026-10-16",
                      "tier_at_review": "qualified"}}
    out = qg.build(_warehouse(), _returns, notes=notes, today=TODAY)
    grow = next(c for c in out["candidates"] if c["ticker"] == "GROW")
    assert grow["notes"]["why"] == "w"
    assert "GROW" not in out["needs_review"]
