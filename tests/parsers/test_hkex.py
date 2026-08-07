"""HKEX (.HK) parser tests. Fixtures: tests/fixtures/extracted/hkex/.

The measured problem: bilingual-era filings collapse under the generic
parser. 0285.HK annuals 2015-2017 (English-only) yield ~100 facts each;
FY2018+ (English + Chinese on every label) yield 1-19 — and the FY2024
file's single "fact" is a prose false positive. Under test:

  * labels with trailing CJK ("REVENUE 收入") match the metric vocabulary;
  * RMB’000 (curly apostrophe) reads as thousands, RMB as CNY;
  * the third comparative column of multi-year tables is emitted as
    another prior_year_column instead of being silently discarded.
"""

from parsers import get_parser


def parser():
    return get_parser("0285.HK")


def scan(fixture_text, name, filename="0285.HK_Annual_FY2024.txt"):
    return list(parser().scan(fixture_text("hkex", name), filename))


class TestRouting:
    def test_hk_suffix_routes_to_hkex_parser(self):
        assert type(parser()).__name__ == "HKEXParser"


class TestBilingualLabels:
    def test_statement_lines_match_vocabulary(self, fixture_text):
        facts = scan(fixture_text, "0285_bilingual_statement.txt")
        metrics = {f["metric"] for f in facts}
        assert {"Revenue", "CostOfRevenue", "GrossProfit",
                "ProfitBeforeTax"} <= metrics

    def test_values_and_note_column(self, fixture_text):
        # " REVENUE 收入   5   177,305,549   129,956,992": CJK stripped,
        # the note ref 5 dropped, both year columns kept.
        facts = scan(fixture_text, "0285_bilingual_statement.txt")
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [f["value_raw"] for f in rev] == [177305549.0, 129956992.0]
        assert rev[0]["period"] == "FY2024"

    def test_parenthesised_negatives(self, fixture_text):
        facts = scan(fixture_text, "0285_bilingual_statement.txt")
        cor = [f for f in facts if f["metric"] == "CostOfRevenue"]
        assert cor[0]["value_raw"] == -165004243.0


class TestUnitsAndCurrency:
    def test_rmb_curly_thousands(self, fixture_text):
        facts = scan(fixture_text, "0285_bilingual_statement.txt")
        assert {f["units_hint"] for f in facts} == {"thousands"}

    def test_rmb_maps_to_cny(self, fixture_text):
        facts = scan(fixture_text, "0285_bilingual_statement.txt")
        assert {f["currency"] for f in facts} == {"CNY"}

    def test_units_found_below_head_window(self, fixture_text):
        # Real 0285 filings declare RMB’000 at line ~94, beyond the 80-line
        # head window the generic parser scans.
        text = "\n".join(["cover prose"] * 90) + "\n" + \
               fixture_text("hkex", "0285_bilingual_statement.txt")
        facts = list(parser().scan(text, "0285.HK_Annual_FY2024.txt"))
        assert facts
        assert {f["units_hint"] for f in facts} == {"thousands"}
        assert {f["currency"] for f in facts} == {"CNY"}


class TestParenthesizedTableDeclarations:
    def test_in_thousands_beats_head_prose(self):
        # NetEase 6-Ks: head prose says "RMB24.0 billion (US$3.3 billion)"
        # but the statement tables declare "(in thousands)".
        text = ("Net revenues were RMB24.0 billion (US$3.3 billion).\n"
                + "\n" * 5 +
                "UNAUDITED CONDENSED CONSOLIDATED BALANCE SHEETS\n"
                "(in thousands)\n"
                "                          2024        2023\n"
                "Total assets           1,234,567   1,111,111\n")
        facts = list(parser().scan(text, "9999.HK_6K_Q1-2024.txt"))
        assert facts
        assert {f["units_hint"] for f in facts} == {"thousands"}

    def test_rmb_in_thousands_form_below_head(self):
        # Bilibili 20-F table headers: "(RMB, in thousands)", first at line
        # ~574 in the real file — far below the 80-line head window.
        text = ("prose\n" * 90 +
                "SELECTED FINANCIAL DATA\n"
                "                                  (RMB, in thousands)\n"
                "                                  2024        2023\n"
                "Total revenues                 1,234,567   1,111,111\n")
        facts = list(parser().scan(text, "9626.HK_20F_FY2024.txt"))
        assert facts
        assert {f["units_hint"] for f in facts} == {"thousands"}

    def test_all_amounts_in_thousands_form(self):
        # Bilibili 6-K earnings releases: "(All amounts in thousands,
        # except for share and per share data)" — with billions of RMB in
        # the head prose.
        text = ("Total net revenues were RMB5.9 billion (US$800 million).\n"
                + "prose\n" * 90 +
                "UNAUDITED CONDENSED CONSOLIDATED BALANCE SHEETS\n"
                "  (All amounts in thousands, except for share and per share data)\n"
                "                                  2023        2022\n"
                "Total assets                   1,234,567   1,111,111\n")
        facts = list(parser().scan(text, "9626.HK_6K_Q3-2023.txt"))
        assert facts
        assert {f["units_hint"] for f in facts} == {"thousands"}

    def test_in_millions_form_below_head(self):
        # Alibaba HK annuals: "(in millions)", first at line ~3114.
        text = ("prose\n" * 90 +
                "CONSOLIDATED BALANCE SHEET\n"
                "                                    (in millions)\n"
                "                                  2025        2024\n"
                "Total assets                   1,234,567   1,111,111\n")
        facts = list(parser().scan(text, "9988.HK_Annual_FY2025.txt"))
        assert facts
        assert {f["units_hint"] for f in facts} == {"millions"}


class TestThirdComparativeColumn:
    def test_three_columns_emitted(self, fixture_text):
        facts = scan(fixture_text, "0285_five_year_summary.txt")
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [f["value_raw"] for f in rev] == [
            177305549.0, 129956992.0, 107186288.0]
        # the extra column reuses the vocabulary the agent already knows
        assert [f["confidence"] for f in rev] == [
            "statement_line", "prior_year_column", "prior_year_column"]
        assert rev[1]["period"] is None
        assert rev[2]["period"] is None
