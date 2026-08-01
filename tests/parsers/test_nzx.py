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


class TestNoteColumnStillWorks:
    def test_note_refs_skipped(self, fixture_text):
        facts = scan(fixture_text, "AGL_note_column.txt")
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [f["value_raw"] for f in rev] == [263527.0, 267805.0]
