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
        # the fixture's own name states no period, so neither column has one
        assert [f["period"] for f in rev] == [None, None]

    def test_comparative_is_labelled_prior_year(self, fixture_path):
        # The comparative column is the same period one fiscal year earlier,
        # spelled canonically so it can corroborate that year's own filing.
        text = fixture_path("generic", "clean_statement.txt").read_text()
        facts = list(bf.BaseParser().scan(text, "X.NZ_Annual_FY2024.txt"))
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [(f["period"], f["confidence"]) for f in rev] == [
            ("FY2024", "statement_line"), ("FY2023", "prior_year_column")]

    def test_half_year_comparative_is_prior_half(self, fixture_path):
        text = fixture_path("generic", "clean_statement.txt").read_text()
        facts = list(bf.BaseParser().scan(text, "X.NZ_HalfYear_H1-2025.txt"))
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [f["period"] for f in rev] == ["H1-2025", "H1 FY2024"]

    def test_unknown_period_stays_unknown_on_every_column(self, fixture_path):
        text = fixture_path("generic", "clean_statement.txt").read_text()
        facts = list(bf.BaseParser().scan(text, "X.NZ_Presentation.txt"))
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [f["period"] for f in rev] == [None, None]

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


class TestLineNumbersMatchTheFile:
    def test_form_feed_does_not_start_a_new_line(self):
        # pdftotext writes a form feed at every page break. str.splitlines()
        # treats it as a line break, sed/editors do not; facts.line_no must
        # agree with `sed -n` or every pointer the agent follows is late.
        text = "\fRevenue  10  9\nCost of sales  (4)  (3)\n\fTotal assets  50  40\n"
        facts = list(bf.BaseParser().scan(text, "X.NZ_Annual_FY2024.txt"))
        lines = {f["metric"]: f["line_no"] for f in facts}
        assert lines == {"Revenue": 1, "CostOfRevenue": 2, "TotalAssets": 3}


class TestWrappedLabels:
    TEXT = ("Cash flows from operating activities\n"
            "Net cash generated from\n"
            "  operating activities                              115          179\n"
            "Purchase of property, plant\n"
            "  and equipment                                      (2)          (1)\n"
            "Cash and cash equivalents at 1 January             2,733        2,456\n"
            "Cash and cash equivalents at 31 December           2,141        2,733\n"
            "Cash generated from operations                       547          884\n")

    def facts(self):
        return list(bf.BaseParser().scan(self.TEXT, "X.HK_HalfYear_H1-2025.txt"))

    def test_label_wrapped_onto_the_numbers_line_is_joined(self):
        got = {(f["metric"], f["confidence"]): (f["value_raw"], f["line_no"])
               for f in self.facts()}
        assert got[("OperatingCashFlow", "statement_line")] == (115.0, 3)
        assert got[("CapEx", "statement_line")] == (-2.0, 5)

    def test_opening_cash_balance_is_not_cash(self):
        cash = [(f["value_raw"], f["line_no"]) for f in self.facts()
                if f["metric"] == "CashAndEquivalents" and f["confidence"] == "statement_line"]
        assert cash == [(2141.0, 7)]

    def test_cash_generated_from_operations_is_its_own_metric(self):
        cgo = [f["value_raw"] for f in self.facts()
               if f["metric"] == "CashGeneratedFromOperations" and f["confidence"] == "statement_line"]
        assert cgo == [547.0]
        ocf = [f["line_no"] for f in self.facts() if f["metric"] == "OperatingCashFlow"]
        assert 8 not in ocf


class TestTwoLineEps:
    TEXT = ("(Loss)/profit for the year                         (2,611)      1,105\n"
            "(Loss)/earnings per share\n"
            "Basic and diluted                          7       (1.05)       0.31\n")

    def test_basic_and_diluted_line_inherits_the_eps_label(self):
        facts = list(bf.BaseParser().scan(self.TEXT, "X.HK_Annual_FY2024.txt"))
        got = {(f["metric"], f["confidence"]): f["value_raw"] for f in facts}
        assert got[("EPS", "statement_line")] == -1.05
        assert got[("EPS", "prior_year_column")] == 0.31
        assert got[("NetIncome", "statement_line")] == -2611.0


class TestLoneNoteRefLine:
    TEXT = ("(Loss)/earnings per share                              7\n"
            "Basic and diluted                                              (0.86)       0.23\n")

    def test_note_ref_alone_on_the_label_line_is_not_a_value(self):
        facts = list(bf.BaseParser().scan(self.TEXT, "X.HK_Annual_FY2020.txt"))
        eps = [(f["value_raw"], f["line_no"], f["confidence"]) for f in facts if f["metric"] == "EPS"]
        assert eps == [(-0.86, 2, "statement_line"), (0.23, 2, "prior_year_column")]


class TestTextPeriodOverridesFilename:
    TEXT = ("Interim Report\nFor the six months ended 31 December 2024\n"
            "Condensed consolidated income statement\n"
            "for the six months ended 31 December 2024\n"
            "six months ended 31 December 2024\n"
            "Revenue   12,345   11,000\n")

    def test_statement_wins_over_the_filename(self):
        facts = list(bf.BaseParser().scan(self.TEXT, "X.HK_HalfYear_H1-2024.txt", fy_end_month=6))
        assert [f["period"] for f in facts] == ["H1 FY2025", "H1 FY2024"]

    def test_half_year_file_ignores_annual_comparative_phrases(self):
        text = ("year ended 31 December 2024\n" * 4
                + "six months ended 30 June 2025\n" * 3
                + "Revenue   12,345   11,000\n")
        facts = list(bf.BaseParser().scan(text, "X.HK_HalfYear_H1-2025.txt", fy_end_month=12))
        assert [f["period"] for f in facts] == ["H1 FY2025", "H1 FY2024"]

    def test_untyped_filename_is_never_relabelled_from_text(self):
        # A presentation or shareholder letter quotes "year ended ..." freely;
        # without a report type in the name there is nothing to check it against.
        text = "year ended 31 December 2017\n" * 3 + "Revenue   12,345   11,000\n"
        facts = list(bf.BaseParser().scan(text, "letter-to-shareholders-1q18.txt", fy_end_month=12))
        assert [f["period"] for f in facts] == [None, None]

    def test_filename_stands_when_the_text_is_silent(self):
        facts = list(bf.BaseParser().scan("Revenue   12,345   11,000\n",
                                          "X.HK_HalfYear_H1-2024.txt", fy_end_month=6))
        assert [f["period"] for f in facts] == ["H1-2024", "H1 FY2023"]
