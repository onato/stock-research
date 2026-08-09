"""Units backfill: the guard matters more than the coverage.

58 tickers carry NULL units, so metrics_normalized yields nothing for them.
The temptation is to infer the scale from magnitude or from facts.units_hint.
Both produce 1000x errors, which is why the first test here is a refusal.
"""

import backfill_units
import pytest


def anchored(**inputs):
    return {"inputs": inputs}


class TestRefusal:
    """No consensus means NULL stays NULL. A missing row is obvious."""

    def test_refuses_on_conflicting_anchors(self):
        """Anchors landing on different decades mean the DB is not one scale.

        One column stored in millions and another in absolute units is a data
        bug. Picking the majority would launder it into the screen; refusing
        leaves it visible.
        """
        rows = [{"period": "FY2025", "total_debt": 100.0,
                 "free_cash_flow": 50_000_000.0}]
        dcf = anchored(total_debt=100.0, last_fcf=50.0)
        units, reason = backfill_units.infer("T", rows, dcf)
        assert units is None
        assert reason.startswith("conflicting-anchors")
        # The rationale must name the disagreement so a human can adjudicate.
        assert "last_fcf" in reason
        assert "total_debt" in reason

    def test_refuses_with_a_single_anchor(self):
        """One anchor agreeing on one decade is a coincidence risk."""
        rows = [{"period": "FY2025", "total_debt": 100.0}]
        units, reason = backfill_units.infer("T", rows, anchored(total_debt=100.0))
        assert units is None
        assert reason.startswith("insufficient-anchors")

    def test_refuses_without_a_dcf(self):
        rows = [{"period": "FY2025", "total_debt": 100.0}]
        assert backfill_units.infer("AFI.NZ", rows, None)[0] is None

    def test_refuses_when_no_anchor_matches_any_decade(self):
        rows = [{"period": "FY2025", "total_debt": 137.0,
                 "shares_outstanding": 42.0}]
        dcf = anchored(total_debt=6.1, shares_outstanding=3.3)   # ratios ~22, ~13
        assert backfill_units.infer("T", rows, dcf)[0] is None


class TestInference:
    def test_two_agreeing_anchors_resolve_millions(self):
        """EBO.NZ's real figures: DB and DCF state the same quantities."""
        rows = [{"period": "FY2025", "total_debt": 1371.2,
                 "free_cash_flow": 293.4, "shares_outstanding": 205.0}]
        dcf = anchored(total_debt=1371.2, last_fcf=293.4, shares_outstanding=205.0)
        units, reason = backfill_units.infer("EBO.NZ", rows, dcf)
        assert units == "millions"
        assert "3" in reason           # vote count surfaces in the rationale

    def test_detects_a_thousands_scale(self):
        rows = [{"period": "FY2025", "total_debt": 1371200.0,
                 "free_cash_flow": 293400.0}]
        dcf = anchored(total_debt=1371.2, last_fcf=293.4)
        assert backfill_units.infer("T", rows, dcf)[0] == "thousands"

    def test_detects_absolute_dollars(self):
        rows = [{"period": "FY2025", "total_debt": 1_371_200_000.0,
                 "free_cash_flow": 293_400_000.0}]
        dcf = anchored(total_debt=1371.2, last_fcf=293.4)
        assert backfill_units.infer("T", rows, dcf)[0] == "absolute"

    def test_one_corrupt_period_does_not_veto_a_clean_anchor(self):
        """MELI's real shape: shares are 50.697 in most periods but 3670 in
        Q4 2025, where a cash figure appears to have landed in the column.
        Scanning every period finds the clean match rather than abstaining.
        """
        rows = [
            {"period": "Q4 2025", "shares_outstanding": 3670.0, "total_debt": 6748.0},
            {"period": "Q4 2024", "shares_outstanding": 50.697, "total_debt": 6850.0},
        ]
        dcf = anchored(shares_outstanding=50.697, total_debt=6850.0)
        assert backfill_units.infer("MELI", rows, dcf)[0] == "millions"

    def test_tolerates_rounding_between_db_and_dcf(self):
        rows = [{"period": "FY2025", "total_debt": 516.8,
                 "shares_outstanding": 724.231}]
        dcf = anchored(total_debt=517.0, shares_outstanding=724.2)
        assert backfill_units.infer("OCA.NZ", rows, dcf)[0] == "millions"

    def test_zero_and_missing_values_are_not_anchors(self):
        """A zero debt row cannot vote -- every ratio against it is undefined."""
        rows = [{"period": "FY2025", "total_debt": 0.0, "free_cash_flow": None,
                 "shares_outstanding": 205.0}]
        dcf = anchored(total_debt=0.0, last_fcf=293.4, shares_outstanding=205.0)
        assert backfill_units.infer("T", rows, dcf)[0] is None


class TestNeverUsesUnitsHint:
    """facts.units_hint records the FILING's scale, not the stored value's.

    The parser agent already rescaled before writing core_metrics, so the hint
    describes the input. Trusting it marks 2CC/AGL/SUM/OCA as `thousands`
    when the rows are plainly millions -- the SEK.NZ NZ$411bn bug.
    """

    def test_hint_saying_thousands_does_not_override_anchors(self, mem_db):
        mem_db.execute(
            "INSERT INTO facts (metric, period, value_raw, units_hint) "
            "VALUES ('revenue', 'FY2025', 82000.0, 'thousands')")
        rows = [{"period": "FY2025", "total_debt": 24.0,
                 "shares_outstanding": 67.66}]
        dcf = anchored(total_debt=24.0, shares_outstanding=67.66)
        assert backfill_units.infer("AGL.NZ", rows, dcf)[0] == "millions"

    def test_module_does_not_reference_units_hint(self):
        """A guard against a future edit quietly reintroducing the trap."""
        assert "units_hint" not in _code_of(backfill_units.infer)
        assert "units_hint" not in _code_of(backfill_units.anchor_votes)

    def test_anchor_fields_are_all_balance_or_flow_items(self):
        assert "units_hint" not in {c for _, c in backfill_units.ANCHORS}


def _code_of(fn):
    import inspect
    return inspect.getsource(fn)


class TestApply:
    def test_dry_run_writes_nothing(self, mem_db):
        _seed(mem_db, units=None)
        backfill_units.apply_units(mem_db, "millions", dry_run=True)
        assert _units(mem_db) == [None]

    def test_apply_sets_units_on_null_rows(self, mem_db):
        _seed(mem_db, units=None)
        assert backfill_units.apply_units(mem_db, "millions", dry_run=False) == 1
        assert _units(mem_db) == ["millions"]

    def test_never_overwrites_an_existing_value(self, mem_db):
        _seed(mem_db, units="thousands")
        assert backfill_units.apply_units(mem_db, "millions", dry_run=False) == 0
        assert _units(mem_db) == ["thousands"]

    def test_is_idempotent(self, mem_db):
        _seed(mem_db, units=None)
        backfill_units.apply_units(mem_db, "millions", dry_run=False)
        assert backfill_units.apply_units(mem_db, "millions", dry_run=False) == 0
        assert _units(mem_db) == ["millions"]


def _seed(con, units):
    con.execute(
        "INSERT INTO core_metrics (period, revenue, units) VALUES (?, ?, ?)",
        ["FY2025", 100.0, units])


def _units(con):
    return [r[0] for r in con.execute("SELECT units FROM core_metrics").fetchall()]


class TestLadder:
    @pytest.mark.parametrize(
        ("db_value", "dcf_value", "expected"),
        [
            (100.0, 100.0, "millions"),
            (100_000.0, 100.0, "thousands"),
            (0.1, 100.0, "billions"),
            (100_000_000.0, 100.0, "absolute"),
            (37.0, 100.0, None),          # 0.37x matches no decade
        ],
    )
    def test_single_ratio_classification(self, db_value, dcf_value, expected):
        assert backfill_units.classify(db_value, dcf_value) == expected
