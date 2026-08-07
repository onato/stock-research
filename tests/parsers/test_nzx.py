"""NZX (.NZ) parser tests. Fixtures: tests/fixtures/extracted/nzx/.

The three NZX-specific behaviors under test:
  * a rounding declaration ("... rounded to thousands ($000) ...") anywhere
    in the filing outranks marketing-prose amounts in the head — the AGL.NZ
    bug where "$267.8 million" cover copy set units_hint='millions' for a
    thousands filing;
  * an inline units cell between label and numbers ("Operating revenue  $m
    6,755  6,752" — AIR.NZ) is consumed and sets that line's units;
  * a stated presentation currency ("presented in New Zealand dollars")
    outranks stray head symbols.
"""

from parsers import get_parser


def parser():
    return get_parser("AGL.NZ")


def scan(fixture_text, name, filename="AGL.NZ_Annual_FY2020.txt"):
    return list(parser().scan(fixture_text("nzx", name), filename))


class TestRouting:
    def test_nz_suffix_routes_to_nzx_parser(self):
        assert type(parser()).__name__ == "NZXParser"


class TestUnitsDeclaration:
    def test_rounding_declaration_beats_head_prose(self, fixture_text):
        # Head says "$267.8 million" (marketing); the notes declare
        # "values rounded to thousands ($000)". Thousands must win.
        facts = scan(fixture_text, "AGL_units_declaration.txt")
        assert facts, "expected facts from the statement lines"
        assert {f["units_hint"] for f in facts} == {"thousands"}

    def test_amounts_are_in_form(self, fixture_text):
        facts = scan(fixture_text, "AGL_note_column.txt")
        assert {f["units_hint"] for f in facts} == {"thousands"}

    def test_curly_apostrophe_column_header(self):
        # NZX filings write the column-header form as $’000 (curly) as often
        # as $'000 (straight); both must read as thousands.
        text = ("STATEMENT OF FINANCIAL POSITION\n"
                "                    2020      2019\n"
                "                   $’000     $’000\n"
                "Total assets      150,321   140,654\n")
        facts = list(parser().scan(text, "X_Annual_FY2020.txt"))
        assert {f["units_hint"] for f in facts} == {"thousands"}


class TestInlineUnitsCell:
    def test_units_cell_consumed_and_numbers_captured(self, fixture_text):
        # "Operating revenue   $m    6,755    6,752" yields Revenue facts;
        # today the $m cell ends the segment and the line emits nothing.
        facts = scan(fixture_text, "AIR_inline_units.txt",
                     filename="AIR.NZ_Annual_FY2025.txt")
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [f["value_raw"] for f in rev] == [6755.0, 6752.0]
        assert rev[0]["period"] == "FY2025"

    def test_units_cell_sets_line_units(self, fixture_text):
        facts = scan(fixture_text, "AIR_inline_units.txt",
                     filename="AIR.NZ_Annual_FY2025.txt")
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert {f["units_hint"] for f in rev} == {"millions"}


class TestStatedCurrency:
    def test_presented_in_nz_dollars(self, fixture_text):
        facts = scan(fixture_text, "AGL_units_declaration.txt")
        assert {f["currency"] for f in facts} == {"NZD"}

    def test_presented_in_australian_dollars(self):
        # ANZ.NZ-style: an NZX listing reporting in AUD. The statement must
        # win over the .NZ suffix prior and over any NZ$ symbols.
        text = ("ANNUAL REPORT\n"
                "The financial statements are presented in Australian dollars.\n"
                "Fees of NZ$ 12 apply in New Zealand branches.\n"
                "                        2024      2023\n"
                "Total assets          150,321   140,654\n")
        facts = list(parser().scan(text, "X_Annual_FY2024.txt"))
        assert {f["currency"] for f in facts} == {"AUD"}


class TestProseFigures:
    """Narrative interims (SUM.NZ) state headline figures only in prose.

    The prose pass is deliberately narrow: an amount qualifies only when a
    magnitude word makes its scale explicit AND a metric phrase sits within
    a few words — hints stay per-fact, so marketing amounts can never poison
    the file-level units the way the AGL.NZ head prose once did.
    """

    def test_prose_stated_profit(self):
        # The amount and its metric phrase straddle pdftotext line breaks.
        text = ("We are pleased to have recorded a\n"
                "$127.2 million IFRS net profit after\n"
                "tax for the first half of 2025\n")
        facts = list(parser().scan(text, "SUM.NZ_HalfYear_H1-2025.txt"))
        hits = [f for f in facts if f["metric"] == "NetIncome"]
        assert [f["value_raw"] for f in hits] == [127.2]
        assert hits[0]["units_hint"] == "millions"
        assert hits[0]["confidence"] == "prose"
        assert hits[0]["period"] == "H1-2025"

    def test_phrase_before_amount(self):
        text = "Total revenue of $986.5 million for the year.\n"
        facts = list(parser().scan(text, "SUM.NZ_Annual_FY2024.txt"))
        assert any(f["metric"] == "Revenue" and f["value_raw"] == 986.5
                   for f in facts)

    def test_currency_prefix_wins_over_file_hint(self):
        text = "Reported NZ$45.1 million EBITDA for the period.\n"
        facts = list(parser().scan(text, "X.NZ_Annual_FY2024.txt"))
        ebitda = [f for f in facts if f["metric"] == "EBITDA"]
        assert [f["value_raw"] for f in ebitda] == [45.1]
        assert ebitda[0]["currency"] == "NZD"

    def test_no_magnitude_word_no_fact(self):
        # "$12 apply" has no million/billion/thousand: scale unknown, and
        # unknown scale must never guess (the SEK.NZ incident).
        text = "Fees of $12 apply to net profit statements requested.\n"
        assert list(parser().scan(text, "X.NZ_Annual_FY2024.txt")) == []

    def test_no_metric_phrase_no_fact(self):
        text = "The village cost $30.2 million to construct in 2024.\n"
        assert list(parser().scan(text, "X.NZ_Annual_FY2024.txt")) == []


class TestNoteColumnStillWorks:
    def test_note_refs_skipped(self, fixture_text):
        facts = scan(fixture_text, "AGL_note_column.txt")
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [f["value_raw"] for f in rev] == [263527.0, 267805.0]
