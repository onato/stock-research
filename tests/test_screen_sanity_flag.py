"""A DCF whose own sanity check tripped is flagged on the leaderboard.

scripts/sanity_check.py grades each DCF's implied multiples against the
ticker's own 10-year history and writes `sanity_check.passed`. Six of the
top 50 on 2026-10-31 (0327.HK, 9898.HK, 3339.HK, 2610.HK, 3918.HK, 6603.HK)
carried `passed: false` -- PAX Global's IV implies 19x earnings against a
decade averaging 7x -- and every one of them showed "ok" in the Flags
column, because the screener never read the block. The flag is a warning,
not an exclusion: the row keeps its rank, and the trip reasons ride along
as the badge's tooltip so the reader does not have to open the JSON.

Shapes in the wild (mirrors scripts/run_evals.py): {ran, passed},
{passed: true, ...}, {status: "PASSED"}. `passed: null` is the script
saying its rules do not apply (a REIT has no earnings multiple), which is
not a failure.
"""

import argparse
import importlib.util
import json
import pathlib

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_sanity", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen = _load()

TRIP = "Implied P/E 19.1x exceeds 2x the 10yr average 7.0x"


def _args(root, **over):
    base = {"root": str(root), "only": None, "live": False,
            "stale_days": 45, "drift_pct": 10, "top": 50}
    base.update(over)
    return argparse.Namespace(**base)


def add_ticker(root, ticker, sanity_check="absent"):
    reports = root / ticker / "Reports"
    reports.mkdir(parents=True)
    dcf = {
        "ticker": ticker,
        "valuation_date": screen.today().isoformat(),
        "current_price": 3.41,
        "inputs": {"currency": "HKD", "quote_currency": "HKD"},
        "probability_weighted": {"weighted_iv": 13.07},
    }
    if sanity_check != "absent":
        dcf["sanity_check"] = sanity_check
    (reports / f"{ticker}_DCF.json").write_text(json.dumps(dcf))


def flags_for(tmp_path, sanity_check):
    add_ticker(tmp_path, "0327.HK", sanity_check)
    ranked, unranked, excluded = screen.screen(_args(tmp_path))
    assert ranked, "flag must not demote"
    assert not unranked
    assert not excluded
    return ranked[0]


class TestSanityFlag:
    def test_tripped_check_is_flagged_with_its_reasons(self, tmp_path):
        row = flags_for(tmp_path, {"ran": True, "passed": False,
                                   "trip_reasons": [TRIP]})
        assert "SANITY_FAIL" in row["flags"]
        assert row["sanity_trips"] == [TRIP]
        assert row["upside_pct"] == 283.3   # still ranked, still computed

    def test_passed_check_is_not_flagged(self, tmp_path):
        row = flags_for(tmp_path, {"ran": True, "passed": True,
                                   "trip_reasons": []})
        assert "SANITY_FAIL" not in row["flags"]
        assert row["sanity_trips"] == []

    def test_status_string_shape(self, tmp_path):
        assert "SANITY_FAIL" in flags_for(tmp_path, {"status": "FAILED"})["flags"]

    def test_passed_status_string_is_clean(self, tmp_path):
        assert "SANITY_FAIL" not in flags_for(
            tmp_path, {"status": "PASSED"})["flags"]

    def test_rules_not_applicable_is_not_a_failure(self, tmp_path):
        row = flags_for(tmp_path, {"ran": True, "passed": None,
                                   "checks_replaced": ["pe", "ev_ebitda"]})
        assert "SANITY_FAIL" not in row["flags"]

    def test_absent_block_is_not_flagged_here(self, tmp_path):
        """run_evals already warns on a missing block; no double count."""
        assert "SANITY_FAIL" not in flags_for(tmp_path, "absent")["flags"]

    def test_malformed_block_is_ignored(self, tmp_path):
        assert "SANITY_FAIL" not in flags_for(tmp_path, "yes")["flags"]


class TestSanityRendering:
    def test_badge_carries_the_trip_reasons_as_tooltip(self, tmp_path):
        row = flags_for(tmp_path, {"ran": True, "passed": False,
                                   "trip_reasons": [TRIP, "second <reason>"]})
        html = screen.row_html(1, row, None, {})
        badge = html.split("SANITY_FAIL")[0].rsplit("<span", 1)[1]
        assert 'class="badge warn"' in badge
        assert "19.1x exceeds 2x" in badge
        assert "second &lt;reason&gt;" in badge
        assert '<span class="ok">ok</span>' not in html

    def test_legend_explains_the_flag(self):
        meanings = dict(screen.FLAG_MEANINGS)
        assert "SANITY_FAIL" in meanings
        assert "10-year" in meanings["SANITY_FAIL"] or "10yr" in meanings["SANITY_FAIL"]

    def test_badge_without_reasons_still_renders(self):
        row = {"ticker": "XX", "currency": "USD", "price": 1.0,
               "weighted_iv": 2.0, "upside_pct": 100.0, "days_old": 1,
               "flags": ["SANITY_FAIL"], "price_src": "stored"}
        html = screen.row_html(1, row, None, {})
        assert "SANITY_FAIL" in html
