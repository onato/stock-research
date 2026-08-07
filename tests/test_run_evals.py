"""Tests for run_evals.py — the tier-1 checker itself.

A tolerance bug here silently mis-grades every ticker (and warns are
deliberately not failures, so nobody would notice). These tests pin the
numeric helpers and the check flows over synthetic artifacts.
"""

import json
import runpy
import shutil
import sys
import warnings
from pathlib import Path
from typing import ClassVar

import pytest
import run_evals as E

FIXTURES = Path(__file__).parent / "fixtures"

GOOD_HEADER = ["Period", "Revenue", "GrossProfit", "GrossMargin",
               "NetIncome", "OperatingCashFlow", "CapEx", "FreeCashFlow"]
GOOD_ROWS = [["FY2023", 100, 40, 40.0, 10, 20, 5, 15],
             ["FY2024", 110, 44, 40.0, 11, 22, 6, 16]]


def write_csv(d, ticker, header, rows):
    lines = [",".join(header)] + [",".join(str(c) for c in r) for r in rows]
    (d / "Reports" / f"{ticker}_Metrics.csv").write_text("\n".join(lines) + "\n")


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


class TestCheckMetricsEdges:
    def test_whitespace_only_csv_is_empty_fail(self, make_ticker):
        d = make_ticker("BLANK")
        (d / "Reports" / "BLANK_Metrics.csv").write_text("\n\n")
        card = E.Card()
        E.check_metrics("BLANK", card)
        assert card.checks == [{"id": "csv_parse", "status": "fail",
                                "detail": "Metrics.csv empty"}]

    def test_header_only_csv_is_empty_fail(self, make_ticker):
        checks = csv_checks(make_ticker, ["Period", "Revenue"], [], ticker="HDR")
        assert checks["csv_parse"]["status"] == "fail"
        assert checks["csv_parse"]["detail"] == "Metrics.csv empty"

    def test_no_core_headers_warns_coverage(self, make_ticker):
        """A CSV whose headers map to nothing in the core schema means the
        exporter and the checker disagree about the world — flag it."""
        checks = csv_checks(make_ticker, ["Period", "Widgets", "Sprockets"],
                            [["FY2024", 1, 2]], ticker="NOCORE")
        assert checks["coverage"]["status"] == "warn"
        assert checks["coverage"]["detail"] == "no headers map to core schema"


class TestCheckDcfEdges:
    def test_unparseable_valuation_date_warns(self, make_ticker):
        checks = dcf_checks(make_ticker,
                            minimal_dcf(valuation_date="Jan 1, 2026"))
        assert checks["dcf_valuation_date"]["status"] == "warn"

    def test_iso_valuation_date_passes(self, make_ticker):
        checks = dcf_checks(make_ticker, minimal_dcf(), ticker="ISOD")
        assert checks["dcf_valuation_date"]["status"] == "pass"

    def test_weighted_iv_skip_when_not_declared(self, make_ticker):
        # A file that declares weights but no weighted_iv* key cannot be
        # graded on the recompute — skip, not fail.
        doc = minimal_dcf()
        doc["probability_weighted"] = {"weights": {"base": 0.5, "bull": 0.25,
                                                   "bear": 0.25}}
        checks = dcf_checks(make_ticker, doc)
        assert checks["dcf_weighted_iv"]["status"] == "skip"
        assert checks["dcf_weighted_upside"]["status"] == "skip"

    def test_sanity_check_without_verdict_warns(self, make_ticker):
        checks = dcf_checks(make_ticker,
                            minimal_dcf(sanity_check={"ran": True}))
        assert checks["dcf_sanity_check"]["status"] == "warn"
        assert "no pass/fail verdict" in checks["dcf_sanity_check"]["detail"]

    def test_terminal_pct_over_85_warns_both_forms(self, make_ticker):
        """terminal_pct_of_value is stored as 90 in some files and 0.90 in
        others; both must trip the same >85% flag."""
        for i, t in enumerate((90, 0.9)):
            doc = minimal_dcf()
            doc["valuation"]["base"]["terminal_pct_of_value"] = t
            checks = dcf_checks(make_ticker, doc, ticker=f"TP{i}")
            assert checks["dcf_terminal_pct"]["status"] == "warn"
            assert "base=90%" in checks["dcf_terminal_pct"]["detail"]

    def test_terminal_pct_moderate_passes(self, make_ticker):
        doc = minimal_dcf()
        doc["valuation"]["base"]["terminal_pct_of_value"] = 60
        checks = dcf_checks(make_ticker, doc)
        assert checks["dcf_terminal_pct"]["status"] == "pass"


def units_checks(make_ticker, ticker):
    card = E.Card()
    E.check_units_consistent(ticker, card)
    assert len(card.checks) == 1
    return card.checks[0]


def make_db(d, ticker, rows):
    import duckdb

    con = duckdb.connect(str(d / "Reports" / f"{ticker}.duckdb"))
    con.execute("CREATE TABLE core_metrics (period VARCHAR, revenue DOUBLE)")
    for period, rev in rows:
        con.execute("INSERT INTO core_metrics VALUES (?, ?)", [period, rev])
    con.close()


class TestUnitsConsistent:
    def test_skip_without_db_or_csv(self, make_ticker):
        d = make_ticker("NODB")
        write_csv(d, "NODB", GOOD_HEADER, GOOD_ROWS)
        check = units_checks(make_ticker, "NODB")
        assert (check["status"], check["detail"]) == ("skip", "no database or CSV")

    def test_skip_unreadable_db(self, make_ticker):
        d = make_ticker("BADDB")
        (d / "Reports" / "BADDB.duckdb").write_text("this is not a database")
        write_csv(d, "BADDB", GOOD_HEADER, GOOD_ROWS)
        check = units_checks(make_ticker, "BADDB")
        assert check["status"] == "skip"
        assert check["detail"].startswith("db unreadable")

    def test_skip_when_no_fy_revenue_in_db(self, make_ticker):
        d = make_ticker("NOFY")
        make_db(d, "NOFY", [("Q1-2024", 25.0), ("FY2024", None)])
        write_csv(d, "NOFY", GOOD_HEADER, GOOD_ROWS)
        check = units_checks(make_ticker, "NOFY")
        assert (check["status"], check["detail"]) == ("skip", "no revenue in core_metrics")

    def test_matching_scale_passes(self, make_ticker):
        d = make_ticker("OKU")
        make_db(d, "OKU", [("FY2024", 110.0)])
        write_csv(d, "OKU", GOOD_HEADER, GOOD_ROWS)
        check = units_checks(make_ticker, "OKU")
        assert check["status"] == "pass"

    def test_power_of_1000_gap_fails(self, make_ticker):
        """The regression this check exists for: db in absolute dollars while
        the CSV is in millions silently breaks every dashboard by 1e6."""
        d = make_ticker("MISM")
        make_db(d, "MISM", [("FY2024", 110_000_000.0)])
        write_csv(d, "MISM", GOOD_HEADER, GOOD_ROWS)
        check = units_checks(make_ticker, "MISM")
        assert check["status"] == "fail"
        assert "units mismatch" in check["detail"]

    def test_moderate_disagreement_falls_through_to_warn(self, make_ticker):
        # A 2x gap is a data difference, not a units error: current behavior
        # is the (misleadingly worded) "no comparable FY period" warn.
        d = make_ticker("TWOX")
        make_db(d, "TWOX", [("FY2024", 220.0)])
        write_csv(d, "TWOX", GOOD_HEADER, GOOD_ROWS)
        check = units_checks(make_ticker, "TWOX")
        assert (check["status"], check["detail"]) == ("warn", "no comparable FY period")

    def test_zero_and_absent_periods_skipped_until_a_match(self, make_ticker):
        # FY2022 absent from the CSV and FY2023 zero are both skipped; the
        # first comparable pair (FY2024) decides the verdict.
        d = make_ticker("ZEROES")
        make_db(d, "ZEROES", [("FY2022", 90.0), ("FY2023", 0.0),
                              ("FY2024", 110.0)])
        write_csv(d, "ZEROES", ["Period", "Revenue"],
                  [["FY2023", 0], ["FY2024", 110.0]])
        check = units_checks(make_ticker, "ZEROES")
        assert check["status"] == "pass"

    def test_non_numeric_csv_revenue_warns(self, make_ticker):
        d = make_ticker("NAN")
        make_db(d, "NAN", [("FY2024", 110.0)])
        write_csv(d, "NAN", ["Period", "Revenue"], [["FY2024", "n/a"]])
        check = units_checks(make_ticker, "NAN")
        assert check["status"] == "warn"

    def test_unreadable_csv_skips(self, make_ticker):
        d = make_ticker("DIRCSV")
        make_db(d, "DIRCSV", [("FY2024", 110.0)])
        # a directory satisfies exists() but cannot be opened as a CSV
        (d / "Reports" / "DIRCSV_Metrics.csv").mkdir()
        check = units_checks(make_ticker, "DIRCSV")
        assert check["status"] == "skip"
        assert check["detail"].startswith("csv unreadable")


def health_checks(make_ticker, ticker):
    card = E.Card()
    E.check_health(ticker, card)
    return {c["id"]: c for c in card.checks}


class TestCheckHealth:
    def test_bare_ticker(self, make_ticker):
        checks = health_checks(make_ticker, "BARE")
        assert checks["analysis_present"]["status"] == "warn"
        # fail, not warn: a missing dashboard once let an incomplete run
        # score a perfect 1.0
        assert checks["dashboard_present"]["status"] == "fail"
        assert checks["extracted_nonempty"]["status"] == "skip"

    def test_healthy_ticker(self, make_ticker):
        d = make_ticker("WELL")
        (d / "Reports" / "WELL_Analysis.json").write_text('{"ok": true}')
        (d / "Reports" / "WELL_Dashboard.html").write_text("x" * 6000)
        (d / "Extracted" / "WELL_Annual_FY2024.txt").write_text("t" * 500)
        checks = health_checks(make_ticker, "WELL")
        assert checks["analysis_present"]["status"] == "pass"
        assert checks["dashboard_present"]["status"] == "pass"
        assert checks["extracted_nonempty"]["status"] == "pass"

    def test_unparseable_analysis_fails(self, make_ticker):
        d = make_ticker("BADJ")
        (d / "Reports" / "BADJ_Analysis.json").write_text("{broken")
        checks = health_checks(make_ticker, "BADJ")
        assert checks["analysis_present"]["status"] == "fail"

    def test_tiny_dashboard_warns(self, make_ticker):
        d = make_ticker("TINY")
        (d / "Reports" / "TINY_Dashboard.html").write_text("<html></html>")
        checks = health_checks(make_ticker, "TINY")
        assert checks["dashboard_present"]["status"] == "warn"

    def test_near_empty_extraction_warns_with_names(self, make_ticker):
        d = make_ticker("THIN")
        (d / "Extracted" / "THIN_Annual_FY2024.txt").write_text("short")
        checks = health_checks(make_ticker, "THIN")
        assert checks["extracted_nonempty"]["status"] == "warn"
        assert "THIN_Annual_FY2024.txt" in checks["extracted_nonempty"]["detail"]


def install_good_ticker(make_ticker, ticker="GOOD"):
    d = make_ticker(ticker)
    write_csv(d, ticker, GOOD_HEADER, GOOD_ROWS)
    (d / "Reports" / f"{ticker}_DCF.json").write_text(json.dumps(minimal_dcf()))
    return d


class TestEvaluate:
    def test_full_scorecard_shape(self, make_ticker, pinned_identity):
        install_good_ticker(make_ticker)
        result = E.evaluate("GOOD")
        assert result["ticker"] == "GOOD"
        # prompt-version identity travels with the score so regressions are
        # attributable
        assert result["agents_sha"] == "testsha00000"
        assert result["git_head"] == "testhead"
        ids = [c["id"] for c in result["checks"]]
        # one entry from each check family proves all four ran
        assert "csv_parse" in ids
        assert "dcf_parse" in ids
        assert "units_consistent" in ids
        assert "dashboard_present" in ids
        s = result["summary"]
        assert s["pass"] + s["warn"] + s["fail"] + s["skip"] == len(ids)


class TestAllTickers:
    def test_reports_dirs_only_and_no_spaces(self, patch_repo, make_ticker):
        make_ticker("AAA")
        make_ticker("BBB")
        make_ticker("BAD NAME")
        (patch_repo / "research" / "NOREPORTS").mkdir()
        assert E.all_tickers() == ["AAA", "BBB"]


class TestMain:
    def run(self, monkeypatch, *argv):
        monkeypatch.setattr(sys, "argv", ["run_evals.py", *argv])
        return E.main()

    def test_no_args_prints_usage(self, monkeypatch, capsys):
        assert self.run(monkeypatch) == 2
        assert "Tier-1 eval" in capsys.readouterr().err

    def test_unknown_flag_rejected(self, monkeypatch, capsys):
        assert self.run(monkeypatch, "--bogus") == 2
        assert "Usage:" in capsys.readouterr().err

    def test_writes_scorecard_and_prints_summary(self, make_ticker,
                                                 pinned_identity, monkeypatch,
                                                 capsys):
        install_good_ticker(make_ticker)
        assert self.run(monkeypatch, "GOOD") == 0
        out = capsys.readouterr().out
        assert out.startswith("GOOD: score=")
        scorecards = list(E.SCORES.glob("GOOD_*.json"))
        assert len(scorecards) == 1
        saved = json.loads(scorecards[0].read_text())
        assert saved["ticker"] == "GOOD"

    def test_strict_exits_1_on_fail(self, make_ticker, pinned_identity,
                                    monkeypatch, capsys):
        make_ticker("EMPTYT")  # no CSV/DCF/dashboard -> hard fails
        assert self.run(monkeypatch, "--strict", "EMPTYT") == 1
        assert "FAIL:" in capsys.readouterr().out

    def test_fails_without_strict_still_exit_0(self, make_ticker,
                                               pinned_identity, monkeypatch,
                                               capsys):
        make_ticker("EMPTYT")
        assert self.run(monkeypatch, "EMPTYT") == 0
        assert "FAIL:" in capsys.readouterr().out

    def test_all_runs_every_ticker(self, make_ticker, pinned_identity,
                                   monkeypatch, capsys):
        install_good_ticker(make_ticker, "AAA")
        install_good_ticker(make_ticker, "BBB")
        assert self.run(monkeypatch, "--all") == 0
        out = capsys.readouterr().out
        assert out.startswith("AAA: score=")
        assert "\nBBB: score=" in out

    def test_entrypoint_exits_with_main_status(self, monkeypatch, capsys):
        # No args -> usage -> sys.exit(2), before SCORES is touched.
        monkeypatch.setattr(sys, "argv", ["run_evals.py"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(SystemExit) as ei:
                runpy.run_module("run_evals", run_name="__main__")
        assert ei.value.code == 2


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
