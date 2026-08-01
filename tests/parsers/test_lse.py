"""LSE (.L) parser tests. Fixture: tests/fixtures/extracted/lse/.

The WISE.L bug: annual-report cover copy ("saved our customers c.£1.5
billion") sits inside the 80-line head window and set units_hint='billions'
for a filing whose statements are headed £m. The column-header form must
outrank head prose.
"""

from parsers import get_parser


def parser():
    return get_parser("WISE.L")


def scan(fixture_text, name="WISE_cover_prose.txt"):
    return list(parser().scan(fixture_text("lse", name),
                              "WISE.L_Annual_FY2023.txt"))


class TestRouting:
    def test_l_suffix_routes_to_lse_parser(self):
        assert type(parser()).__name__ == "LSEParser"


class TestUnits:
    def test_pound_column_header_beats_cover_prose(self, fixture_text):
        facts = scan(fixture_text)
        assert facts
        assert {f["units_hint"] for f in facts} == {"millions"}

    def test_pound_thousands_column(self):
        text = ("COMPANY BALANCE SHEET\n"
                "                        2023      2022\n"
                "                       £'000     £'000\n"
                "Total assets          150,321   140,654\n")
        facts = list(parser().scan(text, "X_Annual_FY2023.txt"))
        assert {f["units_hint"] for f in facts} == {"thousands"}


class TestStatementFacts:
    def test_statement_lines_captured(self, fixture_text):
        facts = scan(fixture_text)
        rev = [f for f in facts if f["metric"] == "Revenue"]
        assert [f["value_raw"] for f in rev] == [846.1, 559.9]
        assert rev[0]["period"] == "FY2023"

    def test_currency_from_pound_symbol(self, fixture_text):
        facts = scan(fixture_text)
        assert {f["currency"] for f in facts} == {"GBP"}
