"""Tests for check_currency.py's filing-evidence sampling (from_filings)
and the reporting path (main).

The hard-won rules pinned here both come from WISE.L: sort by the PERIOD in
the filename (alphabetical sorting pushed the FY2026 20-F that announced the
USD switch outside the sample), and read newest-first stopping at the first
filing with evidence (pooling across years lets 500+ stale GBP symbols
outvote the current USD reality).
"""

import sys

import check_currency as C


def install(make_ticker, ticker, files):
    d = make_ticker(ticker)
    for name, text in files.items():
        (d / "Extracted" / name).write_text(text)
    return d


def install_db(make_ticker, ticker, currency):
    """A real DuckDB under Reports/ with one core_metrics row."""
    import duckdb
    import schema

    d = make_ticker(ticker)
    con = duckdb.connect(str(d / "Reports" / f"{ticker}.duckdb"))
    con.execute(schema.create_sql())
    if currency is not None:
        con.execute(
            "INSERT INTO core_metrics (period, currency) VALUES ('FY2024', ?)",
            [currency])
    con.close()
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


def run_main(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["check_currency.py", *argv])
    code = C.main()
    return code, capsys.readouterr().out


class TestMain:
    def test_agreement_exits_zero(self, make_ticker, monkeypatch, capsys):
        d = install_db(make_ticker, "OK.NZ", "NZD")
        (d / "Extracted" / "OK.NZ_Annual_FY2025.txt").write_text(
            "presented in New Zealand dollars. Revenue NZ$ 1,234.")
        code, out = run_main(monkeypatch, capsys)
        assert code == 0
        assert "no currency mismatches" in out
        assert "NZDx1" in out  # symbol counts shown in the table

    def test_mismatch_flags_and_exits_one(self, make_ticker, monkeypatch, capsys):
        # Recorded USD but the newest filing states NZD: flag with a
        # ready-to-paste re-run command.
        d = install_db(make_ticker, "BAD.NZ", "USD")
        (d / "Extracted" / "BAD.NZ_Annual_FY2025.txt").write_text(
            "presented in New Zealand dollars")
        code, out = run_main(monkeypatch, capsys)
        assert code == 1
        assert "<-- expected NZD" in out
        assert "make run TICKER=BAD.NZ" in out
        assert "recorded USD, should be NZD" in out

    def test_stated_currency_outranks_suffix_prior(
            self, make_ticker, monkeypatch, capsys):
        # WISE.L-shaped: .L suffix says GBP, but the filing states USD and
        # the record agrees — an explicit statement must beat the prior.
        d = install_db(make_ticker, "X.L", "USD")
        (d / "Extracted" / "X.L_Annual_FY2026.txt").write_text(
            "The reporting currency of the Group is the U.S. dollar.")
        code, out = run_main(monkeypatch, capsys)
        assert code == 0
        assert "GBP" in out  # suffix expectation still displayed
        assert "no currency mismatches" in out

    def test_suffix_prior_used_when_nothing_stated(
            self, make_ticker, monkeypatch, capsys):
        install_db(make_ticker, "Y.NZ", "USD")  # no Extracted files at all
        code, out = run_main(monkeypatch, capsys)
        assert code == 1
        assert "recorded USD, should be NZD" in out

    def test_unknown_suffix_never_flagged(self, make_ticker, monkeypatch, capsys):
        # No stated currency and no suffix mapping -> truth is "?" and the
        # ticker cannot be flagged, whatever was recorded.
        install_db(make_ticker, "Z.XX", "EUR")
        code, out = run_main(monkeypatch, capsys)
        assert code == 0
        assert "?" in out

    def test_bare_ticker_defaults_to_usd(self, make_ticker, monkeypatch, capsys):
        install_db(make_ticker, "ACME", "USD")
        code, out = run_main(monkeypatch, capsys)
        assert code == 0
        assert "no currency mismatches" in out

    def test_argv_filters_to_named_tickers(self, make_ticker, monkeypatch, capsys):
        install_db(make_ticker, "AAA.NZ", "NZD")
        install_db(make_ticker, "BBB.NZ", "USD")  # would be flagged if scanned
        code, out = run_main(monkeypatch, capsys, "AAA.NZ")
        assert code == 0
        assert "AAA.NZ" in out
        assert "BBB.NZ" not in out

    def test_db_without_currency_rows_is_skipped(
            self, make_ticker, monkeypatch, capsys):
        install_db(make_ticker, "EMPTY.NZ", None)
        code, out = run_main(monkeypatch, capsys)
        assert code == 0
        assert "EMPTY.NZ" not in out

    def test_unreadable_db_is_skipped(self, make_ticker, monkeypatch, capsys):
        d = make_ticker("CORRUPT.NZ")
        (d / "Reports" / "CORRUPT.NZ.duckdb").write_text("not a database")
        code, out = run_main(monkeypatch, capsys)
        assert code == 0
        assert "CORRUPT.NZ" not in out
