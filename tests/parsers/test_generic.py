"""End-to-end pins of the generic scan over committed fixtures.

These assert the exact fact tuples the scanner emits today for behavior
that must NOT change in the parser split. Known-bug behaviors (AGL units
from prose, bilingual HK labels, ...) are asserted in their country test
modules as red tests instead — the corpus snapshot guards no-regression.
"""

import build_facts as bf


def scan(path):
    return list(bf.scan_file(path))


class TestCleanStatement:
    def test_metric_coverage(self, fixture_path):
        facts = scan(fixture_path("generic", "clean_statement.txt"))
        metrics = {f["metric"] for f in facts}
        assert metrics == {"Revenue", "CostOfRevenue", "GrossProfit",
                           "OperatingIncome", "ProfitBeforeTax", "NetIncome",
                           "TotalAssets", "TotalLiabilities",
                           "ShareholdersEquity"}

    def test_two_columns_emitted_with_confidence(self, fixture_path):
        facts = scan(fixture_path("generic", "clean_statement.txt"))
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [(f["value_raw"], f["confidence"]) for f in rev] == [
            (263527.0, "statement_line"), (267805.0, "prior_year_column")]
        # prior-year column never carries the filing's period
        assert rev[1]["period"] is None

    def test_units_hint_from_sentence_declaration(self, fixture_path):
        facts = scan(fixture_path("generic", "clean_statement.txt"))
        assert {f["units_hint"] for f in facts} == {"thousands"}

    def test_note_cell_between_label_and_numbers(self, fixture_path):
        facts = scan(fixture_path("generic", "clean_statement.txt"))
        ta = [f for f in facts if f["metric"] == "TotalAssets"]
        assert [f["value_raw"] for f in ta] == [150000.0, 140000.0]

    def test_parenthesised_negatives(self, fixture_path):
        facts = scan(fixture_path("generic", "clean_statement.txt"))
        cor = [f for f in facts if f["metric"] == "CostOfRevenue"]
        assert cor[0]["value_raw"] == -2462.0

    def test_line_numbers_and_context(self, fixture_path):
        facts = scan(fixture_path("generic", "clean_statement.txt"))
        rev = next(f for f in facts if f["metric"] == "Revenue")
        assert rev["line_no"] == 9
        assert "Revenue" in rev["context"]
        assert rev["source_file"] == "clean_statement.txt"


class TestNoteColumnFixture:
    def test_nzx_note_refs_skipped(self, fixture_path):
        # Correct today and must stay correct through the NZX parser work.
        facts = scan(fixture_path("nzx", "AGL_note_column.txt"))
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [f["value_raw"] for f in rev] == [263527.0, 267805.0]

    def test_leading_small_int_stripped_as_note_ref(self, tmp_path):
        # A bare numeric note reference ("5") is not a NOTE_CELL match, so
        # it lands in nums and the leading-small-int heuristic strips it.
        # Pinned CURRENT behavior, collateral damage included: a genuine
        # leading value in 1..99 would be eaten too — revisit per-exchange
        # if a fixture shows that happening.
        f = tmp_path / "X_Annual_FY2024.txt"
        f.write_text("Total assets      5      150,000      140,000\n")
        facts = list(bf.scan_file(f))
        assert [x["value_raw"] for x in facts] == [150000.0, 140000.0]
        assert facts[0]["period"] == "FY2024"
