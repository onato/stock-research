"""scripts/dcf_context.py -- the DCF agent's inputs, printed once.

On TPW.AX the dcf-analyst (Fable, $3.68) spent 12 turns grepping the
same component lines every owner-FCF build needs -- interest income,
lease principal, SBC, buybacks, D&A, diluted shares -- and re-reading the
history it had already been given. This script prints all of it in one
call. Fixtures are trimmed filing excerpts; the DB and Yahoo are stubbed.
"""

import json
from pathlib import Path

import dcf_context
import pytest

FIX = Path(__file__).parent / "fixtures" / "dcf"


@pytest.fixture
def text():
    return (FIX / "TPW_FY2026_components.txt").read_text()


class TestComponents:
    def test_each_component_is_found_with_its_line(self, text):
        hits = dcf_context.grep_components(text)
        by_name: dict[str, dcf_context.Hit] = {}
        for h in hits:
            by_name.setdefault(h.name, h)          # first hit per component
        assert by_name["interest_income"].line.startswith("Interest income")
        assert "Payment of principal portion of lease liabilities" in by_name["lease_principal"].line
        assert "share buy-back" in by_name["buybacks"].line
        assert "Equity-settled share-based payment" in by_name["sbc"].line
        assert by_name["depreciation_amortisation"].line.startswith("Depreciation and amortisation")
        assert by_name["income_tax_paid"].line.startswith("Income tax paid")
        assert by_name["capex_ppe"].line_no > 0

    def test_hits_carry_the_numbers_on_the_line(self, text):
        hits = [h for h in dcf_context.grep_components(text) if h.name == "lease_principal"]
        assert hits[0].values == [-6615.0, -6798.0]        # note ref "5" dropped

    def test_refer_to_note_aside_is_not_a_number(self):
        hits = dcf_context.grep_components(
            "Equity-settled share-based payment expense (refer to note 20)      4,177    4,880\n")
        assert hits[0].values == [4177.0, 4880.0]

    def test_prose_mentions_are_not_hits(self):
        hits = dcf_context.grep_components(
            "The Group's interest income policy is described in note 4.\n")
        assert hits == []


class TestHistoryPivot:
    def test_pivot_sorts_periods_and_marks_fy_basis(self):
        rows = [
            {"period": "H1 FY2026", "revenue": 375.9, "free_cash_flow": 20.0},
            {"period": "FY2025", "revenue": 600.7, "free_cash_flow": 30.1},
            {"period": "FY2024", "revenue": 497.8, "free_cash_flow": 25.0},
        ]
        out = dcf_context.render_history(rows, ["revenue", "free_cash_flow"])
        lines = out.splitlines()
        assert lines[0].split()[:3] == ["period", "revenue", "free_cash_flow"]
        assert [ln.split()[0] for ln in lines[1:]] == ["FY2024", "FY2025", "H1"]  # H1 FY2026 last
        assert "600.7" in lines[2]

    def test_nulls_render_as_dash(self):
        out = dcf_context.render_history([{"period": "FY2024", "revenue": None}], ["revenue"])
        assert out.splitlines()[1].split()[1] == "-"


class TestPrice:
    def test_price_from_yahoo_chart_json(self):
        meta = {"regularMarketPrice": 4.82, "currency": "AUD",
                "regularMarketTime": 1787699400, "marketState": "CLOSED",
                "fiftyTwoWeekHigh": 25.61, "fiftyTwoWeekLow": 4.07}
        p = dcf_context.parse_price(json.dumps({"chart": {"result": [{"meta": meta}]}}))
        assert p.price == 4.82
        assert p.currency == "AUD"
        assert p.as_of.startswith("2026-08-2")
        assert p.low_52w == 4.07

    def test_bad_payload_is_none(self):
        assert dcf_context.parse_price("{}") is None


class TestMemoryLine:
    def test_ticker_memory_line_is_quoted(self, tmp_path):
        (tmp_path / "MEMORY.md").write_text(
            "- [Wise (WISE.L)](wise.md) — Mar-31 FY\n"
            "- [Temple (TPW.AX)](tpw.md) — Jun-30 FY; owner-FCF\n")
        assert dcf_context.memory_line("TPW.AX", tmp_path) == "- [Temple (TPW.AX)](tpw.md) — Jun-30 FY; owner-FCF"
        assert dcf_context.memory_line("XYZ.AX", tmp_path) is None
