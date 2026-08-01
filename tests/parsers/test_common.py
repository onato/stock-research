"""Pins for the exchange-independent parsing vocabulary.

These behaviors must survive the per-exchange parser split bit-for-bit:
number grammar, period-from-filename, line segmentation, and the metric
pattern vocabulary (including its hard-won negative lookaheads).
"""

import build_facts as bf


class TestParseNum:
    def test_dashes_are_zero(self):
        for tok in ("-", "—", "–", ""):
            assert bf.parse_num(tok) == 0.0

    def test_parens_negative(self):
        assert bf.parse_num("(1,234)") == -1234.0
        assert bf.parse_num("(5.5)") == -5.5

    def test_thousands_separators(self):
        assert bf.parse_num("263,527") == 263527.0
        assert bf.parse_num("1,234.5") == 1234.5

    def test_non_numeric_none(self):
        assert bf.parse_num("abc") is None
        assert bf.parse_num("$m") is None


class TestPeriodFromFilename:
    def test_fiscal_year(self):
        assert bf.period_from_filename("AGL.NZ_Annual_FY2020.txt") == "FY2020"

    def test_half_year_separators(self):
        assert bf.period_from_filename("X_HalfYear_H1-2024.txt") == "H1-2024"
        assert bf.period_from_filename("X_HalfYear_H1_2024.txt") == "H1-2024"
        assert bf.period_from_filename("X_Interim_h2 2023.txt") == "H2-2023"

    def test_quarter_uses_space(self):
        # Pinned oddity: quarters format with a space, unlike halves.
        assert bf.period_from_filename("X_Quarterly_Q3-2024.txt") == "Q3 2024"

    def test_no_period(self):
        assert bf.period_from_filename("X_Presentation.txt") is None


class TestSegments:
    def test_note_cell_skipped_when_numbers_follow(self):
        line = ("Revenue from contracts with customers        A2"
                "                 263,527       267,805")
        assert list(bf.segments(line)) == [
            ("Revenue from contracts with customers", [263527.0, 267805.0])]

    def test_interleaved_second_column_starts_fresh_segment(self):
        # Two report pages rendered side by side (HLG.NZ, WISE.L layouts).
        line = "Cargo  487  459  Other comprehensive (loss)/income:"
        assert list(bf.segments(line)) == [("Cargo", [487.0, 459.0])]

    def test_prose_yields_nothing(self):
        line = "This means over the past year we saved our customers money."
        assert list(bf.segments(line)) == []

    def test_indented_label(self):
        line = "   Operating revenue      6,755    6,752"
        assert list(bf.segments(line)) == [("Operating revenue", [6755.0, 6752.0])]


class TestCurrencyDetection:
    def currency_of(self, text):
        from parsers.base import BaseParser
        return BaseParser().currency(text.splitlines())

    def test_nz_dollar_followed_by_space(self):
        # The old regex put a \b after the $, which needs a following word
        # character — so "NZ$ thousands" could never match and currency was
        # None for every NZX filing.
        assert self.currency_of("presented in NZ$ thousands") == "NZD"

    def test_pound_and_euro_symbols(self):
        assert self.currency_of("we saved our customers £1.5 billion") == "GBP"
        assert self.currency_of("in thousands of € unless stated") == "EUR"

    def test_rmb_maps_to_cny(self):
        assert self.currency_of("RMB’000") == "CNY"
        assert self.currency_of("amounts in CNY million") == "CNY"

    def test_dollar_symbols_map_to_iso(self):
        assert self.currency_of("HK$ 1,234") == "HKD"
        assert self.currency_of("S$ 1,234") == "SGD"
        assert self.currency_of("C$ 1,234") == "CAD"

    def test_word_tokens_unchanged(self):
        assert self.currency_of("expressed in NZD") == "NZD"
        assert self.currency_of("amounts in USD millions") == "USD"

    def test_no_currency(self):
        assert self.currency_of("nothing monetary here") is None


class TestPatternVocabulary:
    def find(self, label):
        return [m for m, pats in bf.COMPILED.items()
                if any(r.search(label) for r in pats)]

    def test_total_income_tax_not_revenue(self):
        # HLG.NZ regression: "Total income tax expense" poisoned Revenue.
        assert "Revenue" not in self.find("Total income tax expense")
        assert "Revenue" in self.find("Total income")

    def test_adjusted_ebitda_qualifies(self):
        assert "EBITDA" in self.find("Adjusted EBITDA")

    def test_ocf_phrasings(self):
        for label in ("Net cash flows from operating activities",
                      "Cash generated from operations",
                      "Net cash (used in)/provided by operating activities"):
            assert "OperatingCashFlow" in self.find(label), label

    def test_us_equity_award_tax_phrasing(self):
        assert "EquityAwardTaxes" in self.find(
            "Taxes paid related to net share settlement of equity awards")
