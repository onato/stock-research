"""Tests for run_evals.py — the tier-1 checker itself.

A tolerance bug here silently mis-grades every ticker (and warns are
deliberately not failures, so nobody would notice). These tests pin the
numeric helpers and the check flows over synthetic artifacts.
"""

import json
import shutil
from pathlib import Path
from typing import ClassVar

import run_evals as E

FIXTURES = Path(__file__).parent / "fixtures"


def csv_checks(make_ticker, header, rows, ticker="SYN"):
    d = make_ticker(ticker)
    lines = [",".join(header)] + [",".join(str(c) for c in r) for r in rows]
    (d / "Reports" / f"{ticker}_Metrics.csv").write_text("\n".join(lines) + "\n")
    card = E.Card()
    E.check_metrics(ticker, card)
    return {c["id"]: c for c in card.checks}


def dcf_checks(make_ticker, doc, ticker="SYN"):
    d = make_ticker(ticker)
    (d / "Reports" / f"{ticker}_DCF.json").write_text(json.dumps(doc))
    card = E.Card()
    E.check_dcf(ticker, card)
    return {c["id"]: c for c in card.checks}


def minimal_dcf(**overrides):
    doc = {
        "current_price": 10.0,
        "valuation_date": "2026-01-01",
        "valuation": {
            "base": {"intrinsic_value": 12.0},
            "bull": {"intrinsic_value": 18.0},
            "bear": {"intrinsic_value": 6.0},
        },
        "probability_weighted": {
            "weights": {"base": 0.5, "bull": 0.25, "bear": 0.25},
            "weighted_iv": 12.0,
        },
        "sanity_check": {"ran": True, "passed": True},
        "inputs": {"sbc": 1.0},
    }
    doc.update(overrides)
    return doc


class TestClose:
    def test_within_relative_tolerance(self):
        assert E.close(100, 102)          # 2 <= 3% of 102
        assert not E.close(100, 104)      # 4 > 3% of 104

    def test_zero_and_negatives(self):
        assert E.close(0, 0)
        assert E.close(-100, -102)
        assert not E.close(100, -100)

    def test_explicit_tolerance(self):
        assert E.close(100, 101.9, tol=0.02)
        assert not E.close(100, 103, tol=0.02)


class TestMarginOk:
    def test_percent_form(self):
        assert E.margin_ok(25.0, 25.0, 100.0)      # margin stored as 25 (%)
        assert E.margin_ok(26.4, 25.0, 100.0)      # within 1.5pp
        assert not E.margin_ok(30.0, 25.0, 100.0)  # 5pp off

    def test_fraction_form(self):
        assert E.margin_ok(0.25, 25.0, 100.0)      # margin stored as 0.25
        assert not E.margin_ok(0.30, 25.0, 100.0)

    def test_zero_denominator_passes(self):
        assert E.margin_ok(999.0, 25.0, 0.0)


class TestEpsOk:
    def test_same_units(self):
        # NI 1000m / EPS 2.0 -> implied 500m shares; shares column 500 (m).
        assert E.eps_ok(1000.0, 2.0, 500.0)

    def test_units_ladder(self):
        # Shares recorded in thousands or absolute still reconcile via the
        # powers-of-1000 ladder.
        assert E.eps_ok(1000.0, 2.0, 500_000.0)
        assert E.eps_ok(1000.0, 2.0, 500_000_000.0)

    def test_ladder_bounds(self):
        # implied/shares = 1.7 exactly is accepted; 1.8 is not on any rung.
        assert E.eps_ok(1700.0, 1.0, 1000.0)
        assert not E.eps_ok(1800.0, 1.0, 1000.0)

    def test_degenerate_inputs_pass(self):
        assert E.eps_ok(1000.0, 0.0, 500.0)
        assert E.eps_ok(1000.0, 2.0, 0.0)


class TestCheckMetrics:
    HEADER: ClassVar = ["Period", "Revenue", "GrossProfit", "GrossMargin",
              "NetIncome", "OperatingCashFlow", "CapEx", "FreeCashFlow"]

    def test_clean_csv_passes(self, make_ticker):
        checks = csv_checks(make_ticker, self.HEADER, [
            ["FY2023", 100, 40, 40.0, 10, 20, 5, 15],
            ["FY2024", 110, 44, 40.0, 11, 22, 6, 16],
        ])
        assert checks["csv_parse"]["status"] == "pass"
        assert checks["periods_unique"]["status"] == "pass"
        assert checks["identity_fcf"]["status"] == "pass"
        assert checks["identity_gross_margin"]["status"] == "pass"
        assert checks["continuity_revenue"]["status"] == "pass"

    def test_duplicate_periods_fail(self, make_ticker):
        checks = csv_checks(make_ticker, self.HEADER, [
            ["FY2024", 100, 40, 40.0, 10, 20, 5, 15],
            ["FY2024", 100, 40, 40.0, 10, 20, 5, 15],
        ])
        assert checks["periods_unique"]["status"] == "fail"

    def test_broken_fcf_identity_warns_not_fails(self, make_ticker):
        # Owner-FCF adjustments legitimately break FCF = OCF - capex,
        # so this is a review queue, not a grade.
        checks = csv_checks(make_ticker, self.HEADER, [
            ["FY2024", 100, 40, 40.0, 10, 20, 5, 40],
        ])
        assert checks["identity_fcf"]["status"] == "warn"

    def test_unit_jump_flagged_as_continuity_warn(self, make_ticker):
        # The AMZN-div-20 / SEK.NZ-1000x class of bug: an 8x+ jump between
        # adjacent periods suggests unit drift.
        checks = csv_checks(make_ticker, self.HEADER, [
            ["FY2023", 100, 40, 40.0, 10, 20, 5, 15],
            ["FY2024", 100000, 40000, 40.0, 10000, 20000, 5000, 15000],
        ])
        assert checks["continuity_revenue"]["status"] == "warn"

    def test_missing_csv_fails(self, make_ticker):
        make_ticker("EMPTY")
        card = E.Card()
        E.check_metrics("EMPTY", card)
        assert card.checks[0] == {"id": "csv_parse", "status": "fail",
                                  "detail": "Metrics.csv missing"}


class TestCheckDcf:
    def test_clean_dcf_passes(self, make_ticker):
        checks = dcf_checks(make_ticker, minimal_dcf())
        for cid in ("dcf_parse", "dcf_price", "dcf_scenarios", "dcf_weights",
                    "dcf_weighted_iv", "dcf_sanity_check"):
            assert checks[cid]["status"] == "pass", (cid, checks[cid])

    def test_weighted_iv_mismatch_fails(self, make_ticker):
        doc = minimal_dcf()
        doc["probability_weighted"]["weighted_iv"] = 20.0  # truth is 12.0
        checks = dcf_checks(make_ticker, doc)
        assert checks["dcf_weighted_iv"]["status"] == "fail"

    def test_currency_pooling_dual_series(self, make_ticker, patch_repo):
        # 0285.HK declares weighted_iv_hkd AND weighted_iv_rmb over parallel
        # per-scenario IV series. Each must be matched against the recomputed
        # pool of its own currency suffix, not a single blended number.
        d = make_ticker("0285.HK")
        shutil.copy(FIXTURES / "dcf" / "0285.HK_DCF.json",
                    d / "Reports" / "0285.HK_DCF.json")
        card = E.Card()
        E.check_dcf("0285.HK", card)
        checks = {c["id"]: c for c in card.checks}
        assert checks["dcf_weighted_iv"]["status"] == "pass"
        assert "weighted_iv_hkd" in checks["dcf_weighted_iv"]["detail"]
        assert "weighted_iv_rmb" in checks["dcf_weighted_iv"]["detail"]

    def test_weights_not_summing_fail(self, make_ticker):
        doc = minimal_dcf()
        doc["probability_weighted"]["weights"] = {"base": 0.5, "bull": 0.3,
                                                  "bear": 0.3}
        checks = dcf_checks(make_ticker, doc)
        assert checks["dcf_weights"]["status"] == "fail"

    def test_sanity_check_shapes(self, make_ticker):
        cases = [
            ({"ran": True, "passed": True}, "pass"),
            ({"ran": False}, "fail"),
            ({"status": "PASSED"}, "pass"),
            ({"passed": False}, "warn"),                      # no diagnosis
            ({"passed": False, "fix_applied": "rescaled"}, "pass"),
            (None, "warn"),                                   # section absent
        ]
        for i, (sc, expected) in enumerate(cases):
            doc = minimal_dcf()
            if sc is None:
                del doc["sanity_check"]
            else:
                doc["sanity_check"] = sc
            checks = dcf_checks(make_ticker, doc, ticker=f"SC{i}")
            assert checks["dcf_sanity_check"]["status"] == expected, (sc, checks["dcf_sanity_check"])

    def test_missing_dcf_fails_parse(self, make_ticker):
        make_ticker("NODCF")
        card = E.Card()
        E.check_dcf("NODCF", card)
        assert card.checks[0]["status"] == "fail"


class TestCard:
    def test_score_counts_only_pass_fail(self):
        card = E.Card()
        card.add("a", "pass")
        card.add("b", "fail")
        card.add("c", "warn")
        card.add("d", "skip")
        s = card.summary()
        assert s["score"] == 0.5
        assert (s["pass"], s["warn"], s["fail"], s["skip"]) == (1, 1, 1, 1)

    def test_no_graded_checks_scores_none(self):
        card = E.Card()
        card.add("a", "warn")
        assert card.summary()["score"] is None
