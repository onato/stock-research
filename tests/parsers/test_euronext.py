"""Euronext (.AS) parser tests. Fixture: tests/fixtures/extracted/euronext/.

The FLOW.AS bug: "(in thousands of euro)" is declared as a statement header
— repeated at each statement, first occurrence around line 139 in the real
file, always below the 80-line head window — so units_hint stayed None.
The declaration must apply to the facts below it.
"""

from parsers import get_parser


def parser():
    return get_parser("FLOW.AS")


def scan(fixture_text, name="FLOW_statement_header.txt"):
    return list(parser().scan(fixture_text("euronext", name),
                              "FLOW.AS_Annual_FY2024.txt"))


class TestRouting:
    def test_as_suffix_routes_to_euronext_parser(self):
        assert type(parser()).__name__ == "EuronextParser"


class TestStatementHeaderUnits:
    def test_declaration_below_head_window_applies(self, fixture_text):
        # The fixture places "(in thousands of euro)" at line 84.
        facts = scan(fixture_text)
        assert facts, "expected facts from the statement lines"
        assert {f["units_hint"] for f in facts} == {"thousands"}

    def test_facts_captured_with_period(self, fixture_text):
        facts = scan(fixture_text)
        rev = [f for f in facts if f["metric"] == "Revenue"]  # "Total income"
        assert [f["value_raw"] for f in rev] == [479319.0, 303876.0]
        assert rev[0]["period"] == "FY2024"

    def test_nearest_declaration_above_wins(self):
        # Units can change mid-file (whole-euro remuneration tables vs
        # thousands statements); each fact takes the nearest declaration
        # above it.
        text = ("Report\n"
                "Financial overview                  (in thousands of euro)\n"
                "Total income          479,319       303,876\n"
                "\n"
                "Remuneration table                  (in millions of euro)\n"
                "Total income          1,234         1,111\n")
        facts = list(parser().scan(text, "X_Annual_FY2024.txt"))
        by_line = {f["line_no"]: f["units_hint"] for f in facts}
        assert by_line[3] == "thousands"
        assert by_line[6] == "millions"


class TestCurrency:
    def test_euro_from_declaration(self, fixture_text):
        facts = scan(fixture_text)
        assert {f["currency"] for f in facts} == {"EUR"}
