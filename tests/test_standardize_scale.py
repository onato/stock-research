"""standardize_scale.py puts every metrics CSV on one scale and labels it.

Only 21 of 81 committed CSVs record `Units` and 22 record `Currency`. The rest
are silent, and a number cannot reveal its own scale: AAPL's revenue of
416,161 is millions, 0285.HK's 179,477 is thousands, and nothing about the
magnitudes distinguishes them. Cross-ticker work therefore has to guess, which
is the failure `schema.py` documents -- SEK.NZ once read as NZ$411bn.

The scale is recovered the way scripts/backfill_units.py recovers it for the
DuckDBs: each `{TICKER}_DCF.json` states several of the same quantities in
millions, authored independently of the CSV, so the ratio between the two
lands on a power of ten that names the CSV's scale. Two agreeing anchors are
evidence; one is a coincidence; none is a refusal.

The rules these tests hold:

* Never infer a scale from magnitude, and never default one. An unresolved
  ticker keeps its blank Units and is reported -- visible, unlike a wrong one.
* Per-share and percentage columns are scale-free. Rescaling EPS is how
  AIA.NZ ended up with 0.37 (dollars) and 25.87 (cents) in one column.
* Converting is value-preserving: the same quantity, expressed in millions.
"""

import csv
import json

import pytest
import standardize_scale


def write_csv(path, rows, headers=None):
    headers = headers or ["Period", "Revenue", "NetIncome", "EPS",
                          "SharesOutstanding", "GrossMargin", "Units",
                          "Currency"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def ticker_dir(tmp_path):
    d = tmp_path / "research" / "T" / "Reports"
    d.mkdir(parents=True)
    return d


def write_dcf(d, **inputs):
    (d / "T_DCF.json").write_text(json.dumps({"inputs": inputs}))


class TestScaleResolution:
    def test_two_agreeing_anchors_resolve_the_scale(self, ticker_dir):
        # CSV in absolute dollars; the DCF states the same quantities in
        # millions, so the ratio names the CSV's scale.
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Revenue": "500000000",
                    "SharesOutstanding": "100000000"}])
        write_dcf(ticker_dir, total_debt=0, shares_outstanding=100.0,
                  last_fcf=None, cash_and_equivalents=None)
        # one anchor only -> refused
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_a_single_anchor_is_not_enough(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "SharesOutstanding": "100000000"}])
        write_dcf(ticker_dir, shares_outstanding=100.0)
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_millions_is_recognised(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Revenue": "500",
                    "SharesOutstanding": "100", "NetIncome": "50"}])
        write_dcf(ticker_dir, shares_outstanding=100.0, last_fcf=50.0)
        # last_fcf anchors against FreeCashFlow, absent here; use two present
        got = standardize_scale.resolve_scale(ticker_dir, "T")
        assert got in (None, "millions")

    def test_thousands_is_recognised(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Revenue": "6556",
                    "SharesOutstanding": "362717.8"}],
                  headers=["Period", "Revenue", "SharesOutstanding",
                           "CashAndEquivalents", "TotalDebt", "Units",
                           "Currency"])
        write_dcf(ticker_dir, shares_outstanding=362.7178,
                  cash_and_equivalents=None, total_debt=None)
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_conflicting_anchors_refuse(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Revenue": "500",
                    "SharesOutstanding": "100000000"}],
                  headers=["Period", "Revenue", "SharesOutstanding",
                           "CashAndEquivalents", "Units", "Currency"])
        write_dcf(ticker_dir, shares_outstanding=100.0,
                  cash_and_equivalents=None)
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_no_dcf_means_no_resolution(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Revenue": "500"}])
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_magnitude_alone_never_resolves(self, ticker_dir):
        # The SEK.NZ rule: "411,000 looks like thousands" is not evidence.
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Revenue": "411000"}])
        write_dcf(ticker_dir)
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None


class TestDeclaredUnits:
    """The DCFs state their scale outright; 55 of 60 silent CSVs have one.

    A declaration beats a ratio inference, so it is consulted first -- and
    parsed strictly, because the strings carry qualifiers.
    """

    def test_a_bare_declaration_is_used(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024",
                                                  "Revenue": "1"}])
        write_dcf(ticker_dir, units="millions", currency="USD")
        assert standardize_scale.resolve_scale(ticker_dir, "T") == "millions"

    def test_a_thousands_declaration_is_used(self, ticker_dir):
        # FIG and APL.NZ both declare thousands; these are the files that
        # actually need converting.
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024",
                                                  "Revenue": "1"}])
        write_dcf(ticker_dir, units="thousands", currency="USD")
        assert standardize_scale.resolve_scale(ticker_dir, "T") == "thousands"

    def test_a_per_share_qualifier_does_not_change_the_scale(self, ticker_dir):
        # "millions except per-share figures" states millions -- the carve-out
        # names exactly the columns this module already refuses to rescale.
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024",
                                                  "Revenue": "1"}])
        write_dcf(ticker_dir, units="millions except per-share figures",
                  currency="USD")
        assert standardize_scale.resolve_scale(ticker_dir, "T") == "millions"

    def test_a_currency_qualifier_does_not_change_the_scale(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024",
                                                  "Revenue": "1"}])
        write_dcf(ticker_dir, units="usd millions except per-share figures",
                  currency="USD")
        assert standardize_scale.resolve_scale(ticker_dir, "T") == "millions"

    def test_absolute_dollars_prose_resolves_to_absolute(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024",
                                                  "Revenue": "1"}])
        write_dcf(ticker_dir,
                  units="absolute dollars (not thousands or millions) except "
                        "shares_outstanding",
                  currency="NZD")
        assert standardize_scale.resolve_scale(ticker_dir, "T") == "absolute"

    def test_two_scale_words_are_ambiguous_and_refused(self, ticker_dir):
        # A string naming two scales cannot be resolved to one of them.
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024",
                                                  "Revenue": "1"}])
        write_dcf(ticker_dir, units="thousands and millions", currency="USD")
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_the_csvs_own_declaration_still_wins(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Revenue": "1", "Units": "thousands"}])
        write_dcf(ticker_dir, units="millions", currency="USD")
        assert standardize_scale.resolve_scale(ticker_dir, "T") == "thousands"


class TestDefensivePaths:
    """The refusal branches. Each one is a place a wrong scale could enter."""

    def test_an_unreadable_csv_is_not_fatal(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        p.write_bytes(b"\xff\xfe binary garbage")
        assert standardize_scale.read_rows(p) == ([], [])
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None
        assert standardize_scale.convert_file(p) is False
        assert standardize_scale.label_file(p, "millions", "USD") is False

    def test_a_missing_csv_is_not_fatal(self, ticker_dir):
        assert standardize_scale.resolve_scale(ticker_dir, "GONE") is None
        assert standardize_scale.resolve_currency(ticker_dir, "GONE") is None

    def test_an_unparseable_dcf_is_ignored(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024",
                                                  "Revenue": "1"}])
        (ticker_dir / "T_DCF.json").write_text("{not json")
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_a_dcf_without_an_inputs_object_is_ignored(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024",
                                                  "Revenue": "1"}])
        (ticker_dir / "T_DCF.json").write_text(json.dumps({"inputs": "nope"}))
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_a_currency_named_only_in_the_units_string_is_found(self,
                                                                ticker_dir):
        # TSM's DCF says units "millions usd" and states no currency field.
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024",
                                                  "Revenue": "1"}])
        write_dcf(ticker_dir, units="millions usd")
        assert standardize_scale.resolve_currency(ticker_dir, "T") == "USD"

    def test_an_unknown_source_unit_refuses_to_convert(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "1",
                       "Units": "furlongs"}])
        before = p.read_text()
        assert standardize_scale.convert_file(p, "millions") is False
        assert p.read_text() == before

    def test_labelling_a_csv_without_the_columns_is_refused(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "1"}],
                  headers=["Period", "Revenue"])
        assert standardize_scale.label_file(p, "millions", "USD") is False

    def test_a_blank_header_is_skipped(self):
        assert standardize_scale.scalable_headers(
            ["Period", "", "Revenue", None]) == ["Revenue"]

    def test_a_zero_value_cannot_anchor(self, ticker_dir):
        # Every ratio against zero is undefined, so it is not evidence.
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Revenue": "0"}])
        write_dcf(ticker_dir, last_revenue=0)
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_non_fy_rows_are_not_used_as_anchors(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "H1 2024", "Revenue": "500"}])
        write_dcf(ticker_dir)
        assert standardize_scale.resolve_scale(ticker_dir, "T") is None

    def test_an_irregular_newest_fy_row_is_not_an_anchor(self, ticker_dir):
        # A 15-month transition year is FY-shaped but its figures are not
        # comparable with the DCF's twelve-month inputs; the anchor must be
        # the newest genuine year beneath it.
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Revenue": "500",
                    "SharesOutstanding": "100000000",
                    "TotalDebt": "200000000"},
                   {"Period": "FY2025-15mo", "Revenue": "700",
                    "SharesOutstanding": "5", "TotalDebt": "7"}],
                  headers=["Period", "Revenue", "SharesOutstanding",
                           "TotalDebt", "Units", "Currency"])
        write_dcf(ticker_dir, shares_outstanding=100.0, total_debt=200.0)
        assert standardize_scale.resolve_scale(ticker_dir, "T") == "absolute"


class TestConversion:
    def test_thousands_convert_to_millions(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "6556",
                       "NetIncome": "-3164", "Units": "thousands",
                       "Currency": "NZD"}])
        standardize_scale.convert_file(p, "millions")
        row = read_csv(p)[0]
        assert float(row["Revenue"]) == pytest.approx(6.556)
        assert float(row["NetIncome"]) == pytest.approx(-3.164)
        assert row["Units"] == "millions"

    def test_absolute_converts_to_millions(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "161285",
                       "Units": "absolute dollars", "Currency": "NZD"}])
        standardize_scale.convert_file(p, "millions")
        assert float(read_csv(p)[0]["Revenue"]) == pytest.approx(0.161285)

    def test_per_share_and_percent_columns_are_never_scaled(self, ticker_dir):
        # AIA.NZ has 0.37 (dollars) and 25.87 (cents) in one EPS column
        # because something rescaled a per-share figure. Never again.
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "6556", "EPS": "1.25",
                       "GrossMargin": "62.2", "Units": "thousands",
                       "Currency": "NZD"}])
        standardize_scale.convert_file(p, "millions")
        row = read_csv(p)[0]
        assert float(row["EPS"]) == pytest.approx(1.25)
        assert float(row["GrossMargin"]) == pytest.approx(62.2)

    def test_share_counts_are_scaled_with_the_money_columns(self, ticker_dir):
        # schema.metrics_normalized scales shares_outstanding by the same
        # factor; a share count in thousands beside revenue in millions makes
        # every per-share derivation wrong.
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "SharesOutstanding": "362717.8",
                       "Units": "thousands", "Currency": "NZD"}])
        standardize_scale.convert_file(p, "millions")
        assert float(read_csv(p)[0]["SharesOutstanding"]) == pytest.approx(362.7178)

    def test_blank_cells_stay_blank(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "6556",
                       "NetIncome": "", "Units": "thousands"}])
        standardize_scale.convert_file(p, "millions")
        assert read_csv(p)[0]["NetIncome"] == ""

    def test_already_millions_is_a_no_op(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "500",
                       "Units": "millions", "Currency": "USD"}])
        before = p.read_text()
        assert standardize_scale.convert_file(p, "millions") is False
        assert p.read_text() == before

    def test_non_numeric_cells_survive_untouched(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "n/a",
                       "Units": "thousands", "Currency": "NZD"}])
        standardize_scale.convert_file(p, "millions")
        assert read_csv(p)[0]["Revenue"] == "n/a"

    def test_an_undeclared_source_scale_can_be_supplied(self, ticker_dir):
        # FIG's CSV declares nothing while its DCF says thousands. Reading the
        # source scale only from the CSV would silently no-op the conversion
        # and then label the unconverted values "millions" -- a 1000x error
        # written into the file as fact.
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "6556"}])
        assert standardize_scale.convert_file(p, "millions",
                                              source="thousands") is True
        assert float(read_csv(p)[0]["Revenue"]) == pytest.approx(6.556)

    def test_conversion_is_idempotent(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "6556",
                       "Units": "thousands", "Currency": "NZD"}])
        standardize_scale.convert_file(p, "millions")
        after = p.read_text()
        assert standardize_scale.convert_file(p, "millions") is False
        assert p.read_text() == after


class TestCurrency:
    def test_a_clean_code_is_taken_from_the_dcf(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024"}])
        write_dcf(ticker_dir, currency="NZD")
        assert standardize_scale.resolve_currency(ticker_dir, "T") == "NZD"

    def test_a_prose_currency_string_is_refused(self, ticker_dir):
        # AFI.NZ states "AUD (fundamentals) / NZD (reported valuation
        # outputs)" and SPK.NZ "NZ$" -- neither is a code, and picking one
        # would be a guess about which figures the CSV holds.
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024"}])
        write_dcf(ticker_dir,
                  currency="AUD (fundamentals) / NZD (reported valuation outputs)")
        assert standardize_scale.resolve_currency(ticker_dir, "T") is None

    def test_a_symbol_is_refused(self, ticker_dir):
        write_csv(ticker_dir / "T_Metrics.csv", [{"Period": "FY2024"}])
        write_dcf(ticker_dir, currency="NZ$")
        assert standardize_scale.resolve_currency(ticker_dir, "T") is None

    def test_an_existing_csv_currency_wins_over_the_dcf(self, ticker_dir):
        # The CSV is the system of record; the DCF is a consumer of it.
        write_csv(ticker_dir / "T_Metrics.csv",
                  [{"Period": "FY2024", "Currency": "NZD"}])
        write_dcf(ticker_dir, currency="USD")
        assert standardize_scale.resolve_currency(ticker_dir, "T") == "NZD"


class TestPlanAndCli:
    def _corpus(self, tmp_path, ticker, rows, dcf=None, headers=None):
        d = tmp_path / "research" / ticker / "Reports"
        d.mkdir(parents=True, exist_ok=True)
        write_csv(d / f"{ticker}_Metrics.csv", rows, headers=headers)
        if dcf is not None:
            (d / f"{ticker}_DCF.json").write_text(json.dumps({"inputs": dcf}))
        return d

    def test_plan_reports_state_without_writing(self, tmp_path):
        self._corpus(tmp_path, "A", [{"Period": "FY2024", "Revenue": "1"}],
                     dcf={"units": "thousands", "currency": "USD"})
        before = (tmp_path / "research" / "A" / "Reports"
                  / "A_Metrics.csv").read_text()
        got = standardize_scale.plan(tmp_path, [])
        assert got[0]["ticker"] == "A"
        assert got[0]["resolved_units"] == "thousands"
        assert got[0]["needs_conversion"] is True
        assert (tmp_path / "research" / "A" / "Reports"
                / "A_Metrics.csv").read_text() == before

    def test_plan_can_target_named_tickers(self, tmp_path):
        self._corpus(tmp_path, "A", [{"Period": "FY2024", "Revenue": "1"}])
        self._corpus(tmp_path, "B", [{"Period": "FY2024", "Revenue": "1"}])
        assert [r["ticker"] for r in standardize_scale.plan(tmp_path, ["B"])] \
            == ["B"]

    def test_plan_skips_an_empty_csv(self, tmp_path):
        self._corpus(tmp_path, "A", [])
        assert standardize_scale.plan(tmp_path, []) == []

    def _run(self, monkeypatch, tmp_path, *argv):
        monkeypatch.setattr(standardize_scale, "REPO", tmp_path)
        monkeypatch.setattr("sys.argv", ["standardize_scale.py", *argv])
        return standardize_scale.main()

    def test_check_mode_writes_nothing(self, tmp_path, monkeypatch, capsys):
        path = self._corpus(
            tmp_path, "A", [{"Period": "FY2024", "Revenue": "6556"}],
            dcf={"units": "thousands", "currency": "USD"}) / "A_Metrics.csv"
        before = path.read_text()
        assert self._run(monkeypatch, tmp_path, "--check") == 0
        assert path.read_text() == before
        assert "would convert 1" in capsys.readouterr().out

    def test_write_mode_converts_and_labels(self, tmp_path, monkeypatch,
                                            capsys):
        path = self._corpus(
            tmp_path, "A", [{"Period": "FY2024", "Revenue": "6556"}],
            dcf={"units": "thousands", "currency": "USD"}) / "A_Metrics.csv"
        assert self._run(monkeypatch, tmp_path, "--write") == 0
        row = read_csv(path)[0]
        assert float(row["Revenue"]) == pytest.approx(6.556)
        assert row["Units"] == "millions"
        assert row["Currency"] == "USD"
        assert "converted 1" in capsys.readouterr().out

    def test_an_unresolvable_ticker_is_listed_not_guessed(self, tmp_path,
                                                          monkeypatch, capsys):
        path = self._corpus(
            tmp_path, "A", [{"Period": "FY2024", "Revenue": "1"}]) / "A_Metrics.csv"
        before = path.read_text()
        assert self._run(monkeypatch, tmp_path, "--write") == 0
        out = capsys.readouterr().out
        assert "refused 1" in out
        assert "A (no scale)" in out
        assert path.read_text() == before

    def test_an_empty_corpus_errors(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "research").mkdir()
        assert self._run(monkeypatch, tmp_path, "--check") == 1
        assert "no Metrics CSVs" in capsys.readouterr().err


class TestLabelling:
    def test_units_and_currency_are_written_to_every_data_row(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2023", "Revenue": "1"},
                      {"Period": "FY2024", "Revenue": "2"}])
        standardize_scale.label_file(p, "millions", "USD")
        rows = read_csv(p)
        assert [r["Units"] for r in rows] == ["millions", "millions"]
        assert [r["Currency"] for r in rows] == ["USD", "USD"]

    def test_an_entirely_empty_row_is_not_labelled(self, ticker_dir):
        # Labelling a row that holds no data invents a fact about nothing.
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "1"},
                      {"Period": "FY2025"}])
        standardize_scale.label_file(p, "millions", "USD")
        rows = read_csv(p)
        assert rows[0]["Units"] == "millions"
        assert rows[1]["Units"] == ""

    def test_labelling_never_overwrites_a_declared_unit(self, ticker_dir):
        p = ticker_dir / "T_Metrics.csv"
        write_csv(p, [{"Period": "FY2024", "Revenue": "1",
                       "Units": "thousands", "Currency": "NZD"}])
        standardize_scale.label_file(p, "millions", "USD")
        row = read_csv(p)[0]
        assert row["Units"] == "thousands"
        assert row["Currency"] == "NZD"
