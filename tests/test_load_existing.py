"""Tests for load_existing.py: CSV value parsing and core/KPI routing."""

import load_existing as L


class TestParseNumber:
    def test_plain(self):
        assert L.parse_number("1234") == 1234.0
        assert L.parse_number("12.5") == 12.5

    def test_strips_marks(self):
        assert L.parse_number("1,234") == 1234.0
        assert L.parse_number("$1,000") == 1000.0
        assert L.parse_number("12%") == 12.0

    def test_accounting_parens_negative(self):
        assert L.parse_number("(1,234)") == -1234.0
        assert L.parse_number("(5.5)") == -5.5

    def test_blank_tokens_are_none(self):
        for raw in ("", "-", "--", "N/A", "n/a", "NA", "None", "null", None):
            assert L.parse_number(raw) is None

    def test_unparseable_is_none(self):
        assert L.parse_number("abc") is None


class TestToCore:
    def test_maps_aliased_headers(self):
        core, kpis = L.to_core([
            {"Period": "FY2024", "Revenue": "1,000", "EPSDiluted": "2.5",
             "Units": "millions", "Currency": "NZD"},
        ])
        assert len(core) == 1
        rec = core[0]
        assert rec["period"] == "FY2024"
        assert rec["revenue"] == 1000.0
        assert rec["eps"] == 2.5          # EPSDiluted -> eps via ALIASES
        assert rec["units"] == "millions"
        assert rec["currency"] == "NZD"
        assert kpis == []

    def test_unmapped_numeric_headers_become_kpis(self):
        core, kpis = L.to_core([
            {"Period": "FY2024", "Revenue": "100", "ARR": "250"},
        ])
        assert core[0]["revenue"] == 100.0
        assert kpis == [("FY2024", "ARR", 250.0, None)]

    def test_unmapped_text_headers_dropped(self):
        core, kpis = L.to_core([
            {"Period": "FY2024", "Revenue": "100", "Notes": "restated"},
        ])
        assert kpis == []

    def test_rows_without_period_dropped(self):
        core, kpis = L.to_core([
            {"Period": "", "Revenue": "100"},
            {"Period": "FY2024", "Revenue": "200"},
        ])
        assert [r["period"] for r in core] == ["FY2024"]

    def test_blank_units_currency_are_null(self):
        core, _ = L.to_core([{"Period": "FY2024", "Units": "", "Currency": " "}])
        assert core[0]["units"] is None
        assert core[0]["currency"] is None
