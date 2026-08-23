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
                      "Net cash (used in)/provided by operating activities"):
            assert "OperatingCashFlow" in self.find(label), label

    def test_cash_generated_from_operations_is_not_ocf(self):
        # The pre-tax, pre-interest subtotal (0006.HK FY2025: 547 vs OCF 884).
        found = self.find("Cash generated from operations")
        assert "CashGeneratedFromOperations" in found
        assert "OperatingCashFlow" not in found

    def test_us_equity_award_tax_phrasing(self):
        assert "EquityAwardTaxes" in self.find(
            "Taxes paid related to net share settlement of equity awards")


class TestHalfYearNamedByFiscalYear:
    def test_halfyear_fy_is_the_first_half(self):
        # 18 corpus files are named {T}_HalfYear_FY2025.txt; the period is the
        # half, not the year, or the interim collides with the annual.
        assert bf.period_from_filename("TWL.NZ_HalfYear_FY2025.txt") == "H1-2025"
        assert bf.period_from_filename("X.HK_Interim_FY2024.txt") == "H1-2024"

    def test_quarterly_fy_is_undated(self):
        # Which quarter? The name does not say, so no period is the honest answer.
        assert bf.period_from_filename("X_Quarterly_FY2025.txt") is None

    def test_annual_fy_unchanged(self):
        assert bf.period_from_filename("X.NZ_Annual_FY2025.txt") == "FY2025"


class TestTotalAssetsPattern:
    def test_less_current_liabilities_is_excluded(self):
        rx = bf.COMPILED["TotalAssets"]
        assert any(r.search("total assets") for r in rx)
        assert not any(r.search("total assets less current liabilities") for r in rx)


class TestParenthesisedLabels:
    # IFRS filers write "(Loss)/profit for the year" and "(Loss)/earnings per
    # share" when the sign flipped; 0004.HK FY2024 lost EPS and net income
    # to a label grammar that required a leading letter.
    def test_leading_parenthesis_is_a_label(self):
        assert bf.LABEL_RE.match("(Loss)/profit for the year")

    def test_sign_flip_prefixes_are_normalised_away(self):
        assert bf.normalize_label("(Loss)/earnings per share") == "earnings per share"
        assert bf.normalize_label("Earnings/(loss) per share") == "Earnings per share"
        assert bf.normalize_label("(Loss)/profit attributable to") == "profit attributable to"
        assert bf.normalize_label("Revenue") == "Revenue"


class TestOperatingProfitBeforeDandA:
    # 0004.HK prints "Operating profit before depreciation, amortisation,
    # interest and tax" one line above "Operating profit"; the first is EBITDA.
    def test_before_clause_routes_to_ebitda(self):
        found = self.find("Operating profit before depreciation, amortisation, interest and tax")
        assert "EBITDA" in found
        assert "OperatingIncome" not in found

    def test_plain_operating_profit_unchanged(self):
        assert "OperatingIncome" in self.find("Operating profit")

    def test_bank_deposits_and_cash_is_cash(self):
        assert "CashAndEquivalents" in self.find("Bank deposits and cash")

    def find(self, label):
        return [m for m, pats in bf.COMPILED.items() if any(p.search(label) for p in pats)]


class TestSharesPattern:
    def test_treasury_lines_are_not_share_counts(self):
        # 0388.HK: "Shares held for Share Award Scheme" (-1,228) became the share count.
        for label in ("Shares held for Share Award Scheme", "Shares repurchased",
                      "Shares purchased for Share Award Scheme", "Shares to be issued"):
            assert "SharesOutstanding" not in self.find(label), label
        for label in ("Number of ordinary shares", "Weighted average number of shares",
                      "Ordinary shares in issue", "Shares outstanding", "Issued and fully paid shares"):
            assert "SharesOutstanding" in self.find(label), label

    def test_cash_and_other_cash_equivalents_and_bank_deposits(self):
        assert "CashAndEquivalents" in self.find("Cash and other cash equivalents and bank deposits")
        assert "CashAndEquivalents" in self.find("Cash and bank balances")

    def find(self, label):
        return [m for m, pats in bf.COMPILED.items() if any(p.search(label) for p in pats)]


class TestPeriodFromText:
    """The filing states its own period; the filename only guesses it.
    0016.HK's HalfYear_H1-2024 file is "six months ended 31 December 2024"
    for a June year-end, i.e. H1 FY2025, and ARB.NZ's half-years were one
    fiscal year out the same way."""

    def test_annual_is_labelled_by_its_end_year(self):
        lines = ["Annual Report", "For the year ended 30 June 2025", "x",
                 "for the year ended 30 June 2025", "year ended 30 June 2025",
                 "year ended 30 June 2024"]
        assert bf.period_from_text(lines) == "FY2025"
        assert bf.fiscal_year_end(lines) == 6

    def test_half_year_uses_the_fiscal_year_end(self):
        lines = ["Interim Report", "For the six months ended 31 December 2024",
                 "for the six months ended 31 December 2024",
                 "six months ended 31 December 2024"]
        assert bf.period_from_text(lines, fy_end_month=6) == "H1 FY2025"
        assert bf.period_from_text(lines, fy_end_month=12) == "H2 FY2024"

    def test_quarters_and_nine_months(self):
        q = ["for the three months ended 30 September 2025"] * 3
        assert bf.period_from_text(q, fy_end_month=12) == "Q3 FY2025"
        assert bf.period_from_text(q, fy_end_month=6) == "Q1 FY2026"
        nine = ["for the nine months ended September 30, 2025"] * 3
        assert bf.period_from_text(nine, fy_end_month=12) == "9M FY2025"

    def test_us_date_order_and_ordinals(self):
        lines = ["For the year ended December 31, 2024", "year ended December 31st, 2024",
                 "year ended December 31, 2024"]
        assert bf.period_from_text(lines) == "FY2024"

    def test_expected_span_filters_out_comparative_phrases(self):
        # An interim cites "year ended 31 December 2024" in its comparatives
        # more often than its own half-year line (0087.HK: 95 files flipped).
        lines = ["year ended 31 December 2024"] * 5 + ["six months ended 30 June 2025"] * 3
        assert bf.period_from_text(lines, fy_end_month=12, expected_span=12) == "FY2024"
        assert bf.period_from_text(lines, fy_end_month=12, expected_span=6) == "H1 FY2025"
        assert bf.period_from_text(lines, fy_end_month=12, expected_span=3) is None

    def test_latest_period_wins_over_a_more_cited_prior_one(self):
        # SPOT's "FY2020" 20-F is the 2018 filing: 38 x 2018 against 22 x 2017.
        # Both clear the floor; the later one is the filing's own.
        annual = ["year ended December 31, 2017"] * 22 + ["year ended December 31, 2018"] * 38
        assert bf.period_from_text(annual) == "FY2018"
        lines = ["six months ended 31 December 2025"] * 6 + ["six months ended 30 June 2026"] * 4
        assert bf.period_from_text(lines, fy_end_month=12, expected_span=6) == "H1 FY2026"

    def test_a_rarely_cited_later_period_is_a_quote(self):
        # ARB.NZ FY2016 statutory accounts: 12 x 2016 and 2 x 2019.
        lines = ["year ended 30 June 2016"] * 12 + ["year ended 30 June 2019"] * 2
        assert bf.period_from_text(lines, expected_span=12) == "FY2016"

    def test_rare_forward_looking_mentions_do_not_outrank_the_report_year(self):
        # 0363.HK FY2021: 166 x "year ended 31 December 2021", 3 x "... 2023"
        # (bond maturities). Latest-wins needs a relevance floor.
        lines = ["year ended 31 December 2021"] * 166 + ["year ended 31 December 2023"] * 3
        assert bf.period_from_text(lines, expected_span=12) == "FY2021"

    def test_expected_span_from_filename(self):
        assert bf.expected_span("X_Annual_FY2025.txt") == 12
        assert bf.expected_span("X_HalfYear_H1-2024.txt") == 6
        assert bf.expected_span("X_Interim_FY2024.txt") == 6
        assert bf.expected_span("X_Quarterly_Q3-2025.txt") == 3
        assert bf.expected_span("X_10Q_Q3-2025.txt") == 3
        assert bf.expected_span("X_10K_FY2025.txt") == 12
        assert bf.expected_span("X_Presentation.txt") is None

    def test_interim_without_a_known_year_end_is_unlabelled(self):
        assert bf.period_from_text(["six months ended 31 December 2024"] * 3) is None

    def test_us_three_year_column_header_takes_the_last_year(self):
        # GOOG 10-K: every note is headed "Year Ended December 31, 2013 2014 2015";
        # the filing's year is the last of the run, not the first.
        lines = ["                 Year Ended December 31,   2013      2014      2015"] * 3
        assert bf.period_from_text(lines, expected_span=12) == "FY2015"
        lines = ["Three Months Ended March 31, 2024 2025"] * 3
        assert bf.period_from_text(lines, fy_end_month=12, expected_span=3) == "Q1 FY2025"

    def test_two_mentions_are_not_enough(self):
        # CMO.NZ FY2023: two OCR-mangled "year ended 30 June 2013" lines.
        assert bf.period_from_text(["year ended 30 June 2025"] * 2) is None
