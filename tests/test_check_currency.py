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
        assert "<-- filings state NZD" in out
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

    def test_suffix_prior_is_a_note_not_a_failure(
            self, make_ticker, monkeypatch, capsys):
        # Was: the suffix alone flagged this and exited 1. The currency
        # contract makes that wrong -- the suffix predicts the QUOTE
        # currency, and 16 of ~150 tickers legitimately report in another
        # (ARB.NZ files USD on the NZX). Surface it, but do not demand a
        # re-run of a ticker no evidence contradicts.
        install_db(make_ticker, "Y.NZ", "USD")  # no Extracted files at all
        code, out = run_main(monkeypatch, capsys)
        assert code == 0
        assert "not listed in" in out
        assert "reports USD, listed where NZD is usual" in out
        assert "make run TICKER=Y.NZ" not in out

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

    def test_multiple_recorded_currencies_flagged_ambiguous(
            self, make_ticker, monkeypatch, capsys):
        # A DB carrying two distinct currencies means adjudication went
        # wrong somewhere; silently reporting whichever row came first
        # would let the ambiguity pass as agreement.
        import duckdb
        d = install_db(make_ticker, "AMB.NZ", "NZD")
        con = duckdb.connect(str(d / "Reports" / "AMB.NZ.duckdb"))
        con.execute(
            "INSERT INTO core_metrics (period, currency) VALUES ('FY2025', 'AUD')")
        con.close()
        code, out = run_main(monkeypatch, capsys)
        assert code == 1
        assert "ambiguous" in out
        assert "AUD" in out
        assert "NZD" in out

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


class TestCurrencyNameFalsePositives:
    """Real corpus regressions: a company or person's name contains "yen",
    and an FX-risk disclosure names a currency the filer does not report in.

    Both made `make check-currency` demand a re-run of a correct ticker.
    14 of 78 tickers were flagged; 9 of those were this bug, so the report
    was noise and the 5 real findings (ARB.NZ, BIT.NZ, FCT.NZ, HFL.NZ,
    MHJ.NZ) were invisible inside it.
    """

    def test_company_name_containing_yen_is_not_a_currency(self, make_ticker):
        # RX.V: "BioSyent Inc." -- the bare `yen` alternative matched the
        # substring inside the company's own name, and the preceding
        # "functional currency ... is the" lead bridged the gap.
        install(make_ticker, "T", {
            "T_Quarterly_Q3-2023.txt":
                "BioSyent Inc.\n\nInterim unaudited financial statements.\n"
                "The functional currency of the Company is the Canadian "
                "dollar.",
        })
        stated, _ = C.from_filings("T")
        assert stated == "CAD"

    def test_person_name_containing_yen_is_not_a_currency(self, make_ticker):
        # ADBE: CEO Shantanu Narayen appears 125 times.
        install(make_ticker, "T", {
            "T_Annual_FY2024.txt":
                "Shantanu Narayen, Chief Executive Officer.\n"
                "Amounts are in U.S. dollars.",
        })
        stated, _ = C.from_filings("T")
        assert stated == "USD"

    def test_fx_risk_disclosure_is_not_a_reporting_currency_statement(
            self, make_ticker):
        # ADBE 10-Q: "foreign currencies, including the euro and the
        # japanese yen, which decreased revenue" is a risk disclosure. It
        # must not be read as a statement of reporting currency.
        install(make_ticker, "T", {
            "T_Annual_FY2024.txt":
                "We are exposed to foreign currencies, including the euro "
                "and the Japanese yen, which decreased revenue by 2%.",
        })
        stated, _ = C.from_filings("T")
        assert stated is None

    def test_a_real_yen_reporting_statement_still_matches(self, make_ticker):
        # The fix must not cost us the true positive it was there for.
        install(make_ticker, "T", {
            "T_Annual_FY2024.txt":
                "These statements are presented in Japanese yen.",
        })
        stated, _ = C.from_filings("T")
        assert stated == "JPY"


class TestSuffixPriorIsNotEvidence:
    """The listing suffix predicts the QUOTE currency, not the REPORTING one.

    16 of ~150 corpus tickers report in a currency they are not quoted in
    (the currency contract, commit d9bf113a). Falling back to the suffix
    when the filings state nothing turns every one of those into a standing
    demand to re-run a ticker that is already correct -- 9999.HK (RMB/HKD),
    ARB.NZ (USD/NZD) and HFL.NZ (GBP/NZD) were each flagged that way.

    A disagreement with the suffix alone is a NOTE. Only a disagreement with
    what the filings actually STATE is a problem worth a re-run.
    """

    def test_suffix_disagreement_alone_is_not_a_problem(self, make_ticker):
        # Filings state nothing; recorded USD against a .NZ suffix. That is
        # ARB.NZ, and it is correct -- the suffix must not overrule it.
        install_db(make_ticker, "ARB.NZ", "USD")
        install(make_ticker, "ARB.NZ", {
            "ARB.NZ_Annual_FY2024.txt": "Revenue grew strongly this year.",
        })
        problems, _ = C.audit(["ARB.NZ"])
        assert problems == []

    def test_filing_disagreement_is_still_a_problem(self, make_ticker):
        # The filings SAY New Zealand dollars but GBP was recorded. That is
        # a real error and must survive the change.
        install_db(make_ticker, "T.NZ", "GBP")
        install(make_ticker, "T.NZ", {
            "T.NZ_Annual_FY2024.txt": "presented in New Zealand dollars",
        })
        problems, _ = C.audit(["T.NZ"])
        assert [(t, got, want) for t, got, want in problems] == [
            ("T.NZ", "GBP", "NZD")]

    def test_suffix_disagreement_is_reported_as_a_note(self, make_ticker):
        # Not a problem, but still worth surfacing: it is how a genuine
        # cross-currency filer is told apart from a silent mistake.
        install_db(make_ticker, "ARB.NZ", "USD")
        install(make_ticker, "ARB.NZ", {
            "ARB.NZ_Annual_FY2024.txt": "Revenue grew strongly this year.",
        })
        _, notes = C.audit(["ARB.NZ"])
        assert [t for t, _, _ in notes] == ["ARB.NZ"]


class TestNotesCheckedAgainstTheDcf:
    """A cross-currency note is only benign if the DCF agrees with it.

    The DB and the DCF each record a reporting currency independently, and
    nothing compared them. BIT.NZ and FCT.NZ record GBP in `core_metrics`
    while their DCFs say NZD -- the exact silent split the currency
    contract exists to prevent, and it survived the contract because the
    contract only ever looked inside one file.
    """

    def _dcf(self, d, ticker, **inputs):
        import json
        (d / "Reports" / f"{ticker}_DCF.json").write_text(
            json.dumps({"inputs": inputs}))

    def test_dcf_disagreeing_with_the_db_is_a_problem(self, make_ticker):
        # BIT.NZ-shaped: DB says GBP, DCF says NZD. One of them is wrong.
        d = install_db(make_ticker, "BIT.NZ", "GBP")
        self._dcf(d, "BIT.NZ", currency="NZD", quote_currency="NZD")
        problems, _ = C.audit(["BIT.NZ"])
        assert [(t, got, want) for t, got, want in problems] == [
            ("BIT.NZ", "GBP", "NZD")]

    def test_dcf_agreeing_leaves_it_a_note(self, make_ticker):
        # ARB.NZ-shaped: DB says USD, DCF agrees and records the NZD quote
        # plus an fx_note. Deliberate and documented -- a note, not a fault.
        d = install_db(make_ticker, "ARB.NZ", "USD")
        self._dcf(d, "ARB.NZ", currency="USD", quote_currency="NZD",
                  fx_note="USD/NZD 1.63 at 2026-09-01, Yahoo NZDUSD=X")
        problems, notes = C.audit(["ARB.NZ"])
        assert problems == []
        assert [t for t, _, _ in notes] == ["ARB.NZ"]

    def test_cross_currency_without_an_fx_note_is_a_problem(self, make_ticker):
        # Reporting currency != quote currency and no fx_note: the upside
        # is being computed across two currencies with no rate recorded.
        d = install_db(make_ticker, "ARB.NZ", "USD")
        self._dcf(d, "ARB.NZ", currency="USD", quote_currency="NZD")
        problems, _ = C.audit(["ARB.NZ"])
        assert [t for t, _, _ in problems] == ["ARB.NZ"]

    def test_no_dcf_yet_is_not_a_problem(self, make_ticker):
        # Research in progress -- the DCF stage has not run.
        install_db(make_ticker, "NEW.NZ", "USD")
        problems, notes = C.audit(["NEW.NZ"])
        assert problems == []
        assert [t for t, _, _ in notes] == ["NEW.NZ"]
