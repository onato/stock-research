"""Tests for check_currency.py's filing-evidence sampling (from_filings).

The hard-won rules pinned here both come from WISE.L: sort by the PERIOD in
the filename (alphabetical sorting pushed the FY2026 20-F that announced the
USD switch outside the sample), and read newest-first stopping at the first
filing with evidence (pooling across years lets 500+ stale GBP symbols
outvote the current USD reality).
"""

import check_currency as C


def install(make_ticker, ticker, files):
    d = make_ticker(ticker)
    for name, text in files.items():
        (d / "Extracted" / name).write_text(text)
    return d


class TestStatedPatterns:
    def test_presented_in_form(self, make_ticker):
        install(make_ticker, "T", {
            "T_Annual_FY2024.txt":
                "These statements are presented in New Zealand dollars.",
        })
        stated, _ = C.from_filings("T")
        assert stated == "NZD"

    def test_reporting_currency_is_form_with_punctuated_us(self, make_ticker):
        # WISE.L FY2026 regression: "The reporting currency of the Group is
        # the U.S. dollar" was missed by an earlier pattern set.
        install(make_ticker, "T", {
            "T_Annual_FY2026.txt":
                "The reporting currency of the Group is the U.S. dollar.",
        })
        stated, _ = C.from_filings("T")
        assert stated == "USD"

    def test_thousands_of_qualifier(self, make_ticker):
        install(make_ticker, "T", {
            "T_Annual_FY2024.txt": "expressed in thousands of euro",
        })
        stated, _ = C.from_filings("T")
        assert stated == "EUR"


class TestSampling:
    def test_newest_statement_outranks_old_symbols(self, make_ticker):
        # Older filings full of GBP symbols; the newest states USD. The
        # newest wins because reading is newest-first and stops at the
        # first filing that names its currency.
        install(make_ticker, "T", {
            "T_Annual_FY2024.txt": "£1,000 " * 50,
            "T_Annual_FY2026.txt":
                "The reporting currency of the Group is the U.S. dollar.",
        })
        stated, counts = C.from_filings("T")
        assert stated == "USD"
        assert "GBP" not in counts

    def test_period_sorting_not_alphabetical(self, make_ticker):
        # Alphabetically "T_HalfYear_H1-2027.txt" > "T_Annual_FY2026.txt",
        # but FY-period sorting must place H1-2027 newest. With sample=1
        # only the true newest is read.
        install(make_ticker, "T", {
            "T_Annual_FY2026.txt": "presented in New Zealand dollars",
            "T_HalfYear_H1-2027.txt": "presented in Australian dollars",
        })
        stated, _ = C.from_filings("T", sample=1)
        assert stated == "AUD"

    def test_annual_outranks_interim_within_a_year(self, make_ticker):
        install(make_ticker, "T", {
            "T_HalfYear_H1-2026.txt": "presented in Australian dollars",
            "T_Annual_FY2026.txt": "presented in New Zealand dollars",
        })
        stated, _ = C.from_filings("T", sample=1)
        assert stated == "NZD"

    def test_symbol_counts_require_adjacent_digit(self, make_ticker):
        # "NZ$" in prose without a number is not a value; the counter only
        # counts symbol-followed-by-digit occurrences.
        install(make_ticker, "T", {
            "T_Annual_FY2024.txt":
                "Amounts in NZ$ unless stated. Revenue NZ$ 1,234 and NZ$567.",
        })
        _, counts = C.from_filings("T")
        assert counts == {"NZD": 2}

    def test_no_filings(self, patch_repo):
        assert C.from_filings("NOPE") == (None, {})

    def test_statutory_filter_skips_presentations(self, make_ticker):
        install(make_ticker, "T", {
            "T_Presentation_FY2026.txt": "presented in Australian dollars",
            "T_Annual_FY2024.txt": "presented in New Zealand dollars",
        })
        stated, _ = C.from_filings("T", sample=1)
        assert stated == "NZD"
