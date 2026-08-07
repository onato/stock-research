"""Tests for load_existing.py: CSV value parsing, core/KPI routing, and the
legacy-CSV -> DuckDB backfill (read_csv / write_db / main)."""

import sys

import duckdb
import load_existing as L
import schema


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
        _core, kpis = L.to_core([
            {"Period": "FY2024", "Revenue": "100", "Notes": "restated"},
        ])
        assert kpis == []

    def test_rows_without_period_dropped(self):
        core, _kpis = L.to_core([
            {"Period": "", "Revenue": "100"},
            {"Period": "FY2024", "Revenue": "200"},
        ])
        assert [r["period"] for r in core] == ["FY2024"]

    def test_blank_units_currency_are_null(self):
        core, _ = L.to_core([{"Period": "FY2024", "Units": "", "Currency": " "}])
        assert core[0]["units"] is None
        assert core[0]["currency"] is None

    def test_units_kept_verbatim_never_rescaled(self):
        # load_existing must not interpret units: scaling to the canonical
        # millions happens only in the metrics_normalized view, where an
        # unknown string resolves to NULL (the SEK.NZ 1000x rule). Rewriting
        # or guessing here would smuggle an assumed scale into the DB.
        core, _ = L.to_core([
            {"Period": "FY2024", "Revenue": "400", "Units": "NZ$000"},
        ])
        assert core[0]["units"] == "NZ$000"
        assert core[0]["revenue"] == 400.0   # raw value, not scaled

    def test_falsy_header_keys_skipped(self):
        # DictReader yields '' keys for trailing commas and a None restkey
        # for overlong rows; neither may crash or leak into core/kpis.
        core, kpis = L.to_core([
            {"Period": "FY2024", "Revenue": "1", "": "9", None: ["stray"]},
        ])
        assert core[0]["revenue"] == 1.0
        assert kpis == []

    def test_unmapped_columns_use_missing_period_as_empty(self):
        # KPI rows read Period via row.get("Period", ""), so a CSV whose
        # period column is spelled differently yields KPI rows keyed "".
        _core, kpis = L.to_core([{"period": "FY2024", "ARR": "5"}])
        assert kpis == [("", "ARR", 5.0, None)]


CSV_TEXT = (
    "Period,Revenue,EPSDiluted,ARR,Notes,Units,Currency\n"
    "FY2023,900,2.1,200,,thousands,NZD\n"
    'FY2024,"1,000",2.5,250,restated,thousands,NZD\n'
)


class TestReadCsv:
    def test_rows_headers_and_unmapped(self, tmp_path):
        p = tmp_path / "SYN_Metrics.csv"
        p.write_text(CSV_TEXT)
        rows, headers, unmapped = L.read_csv(p)
        assert len(rows) == 2
        assert headers == ["Period", "Revenue", "EPSDiluted", "ARR", "Notes",
                           "Units", "Currency"]
        # aliased headers (EPSDiluted) are mapped; only true KPIs are not
        assert unmapped == ["ARR", "Notes"]

    def test_blank_header_from_trailing_comma_not_unmapped(self, tmp_path):
        p = tmp_path / "SYN_Metrics.csv"
        p.write_text("Period,Revenue,\nFY2024,100,\n")
        _rows, headers, unmapped = L.read_csv(p)
        assert headers == ["Period", "Revenue", ""]
        assert unmapped == []

    def test_bad_encoding_does_not_raise(self, tmp_path):
        # legacy CSVs occasionally carry stray bytes; errors="replace" must
        # keep the load alive rather than abort the whole backfill
        p = tmp_path / "SYN_Metrics.csv"
        p.write_bytes(b"Period,Revenue\nFY2024,1\xff00\n")
        rows, _headers, _unmapped = L.read_csv(p)
        assert rows[0]["Period"] == "FY2024"

    def test_empty_file_yields_no_headers(self, tmp_path):
        p = tmp_path / "SYN_Metrics.csv"
        p.write_text("")
        rows, headers, unmapped = L.read_csv(p)
        assert (rows, list(headers), unmapped) == ([], [], [])


def load_syn(make_ticker, ticker="SYN", text=CSV_TEXT):
    d = make_ticker(ticker)
    (d / "Reports" / f"{ticker}_Metrics.csv").write_text(text)
    return d


class TestWriteDb:
    def test_core_and_kpi_rows_land(self, make_ticker):
        d = load_syn(make_ticker)
        rows, _h, _u = L.read_csv(d / "Reports" / "SYN_Metrics.csv")
        core, kpis = L.to_core(rows)
        db = L.write_db("SYN", core, kpis)
        assert db == d / "Reports" / "SYN.duckdb"

        con = duckdb.connect(str(db))
        got = con.execute(
            "SELECT period, revenue, eps, units, currency FROM core_metrics"
            " ORDER BY period").fetchall()
        assert got == [("FY2023", 900.0, 2.1, "thousands", "NZD"),
                       ("FY2024", 1000.0, 2.5, "thousands", "NZD")]
        assert con.execute(
            "SELECT period, name, value, unit FROM kpis ORDER BY period"
        ).fetchall() == [("FY2023", "ARR", 200.0, None),
                        ("FY2024", "ARR", 250.0, None)]
        con.close()

    def test_reload_replaces_not_appends(self, make_ticker):
        # backfill is rerun whenever a CSV changes; stale rows must go
        load_syn(make_ticker)
        core = [dict(dict.fromkeys(schema.CORE_NAMES),
                     period="FY2024", revenue=1.0)]
        L.write_db("SYN", core, [("FY2024", "ARR", 1.0, None)])
        db = L.write_db("SYN", core, [("FY2024", "ARR", 2.0, None)])

        con = duckdb.connect(str(db))
        assert con.execute("SELECT count(*) FROM core_metrics").fetchone()[0] == 1
        assert con.execute("SELECT value FROM kpis").fetchone()[0] == 2.0
        con.close()

    def test_unknown_units_resolve_null_in_normalized_view(self, make_ticker):
        # End-to-end pin of the units convention: the loader stores whatever
        # the legacy CSV said, and metrics_normalized turns a known scale
        # into millions but an unknown one into NULL — never a guess.
        load_syn(make_ticker)
        blank = dict.fromkeys(schema.CORE_NAMES)
        core = [
            dict(blank, period="FY2022", revenue=400.0, units="thousands"),
            dict(blank, period="FY2023", revenue=400.0, units="millions"),
            dict(blank, period="FY2024", revenue=400.0, units="NZ$000"),
            dict(blank, period="FY2025", revenue=400.0, units=None),
        ]
        db = L.write_db("SYN", core, [])
        con = duckdb.connect(str(db))
        got = dict(con.execute(
            "SELECT period, revenue FROM metrics_normalized").fetchall())
        con.close()
        assert got["FY2022"] == 0.4
        assert got["FY2023"] == 400.0
        assert got["FY2024"] is None
        assert got["FY2025"] is None

    def test_empty_inputs_leave_tables_empty(self, make_ticker):
        load_syn(make_ticker)
        db = L.write_db("SYN", [], [])
        con = duckdb.connect(str(db))
        assert con.execute("SELECT count(*) FROM core_metrics").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM kpis").fetchone()[0] == 0
        con.close()


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["load_existing.py", *argv])
    return L.main()


class TestMain:
    def test_named_ticker_writes_db(self, make_ticker, monkeypatch, capsys):
        d = load_syn(make_ticker)
        assert run_main(monkeypatch, "SYN") == 0
        assert (d / "Reports" / "SYN.duckdb").exists()
        out = capsys.readouterr().out
        assert "1 ticker(s) loaded, 0 failed" in out
        # unmapped headers are surfaced, not silently dropped
        assert "ARR" in out

    def test_missing_csv_warns_on_stderr(self, patch_repo, monkeypatch, capsys):
        assert run_main(monkeypatch, "NOPE") == 0
        cap = capsys.readouterr()
        assert "no metrics CSV for NOPE" in cap.err
        assert "0 ticker(s) loaded" in cap.out

    def test_report_mode_writes_nothing(self, make_ticker, monkeypatch, capsys):
        d = load_syn(make_ticker)
        assert run_main(monkeypatch, "SYN", "--report") == 0
        assert not (d / "Reports" / "SYN.duckdb").exists()
        assert "no databases written" in capsys.readouterr().out

    def test_no_args_globs_every_metrics_csv(self, make_ticker, monkeypatch):
        d1 = load_syn(make_ticker, "AAA.NZ")
        d2 = load_syn(make_ticker, "BBB.NZ")
        assert run_main(monkeypatch) == 0
        assert (d1 / "Reports" / "AAA.NZ.duckdb").exists()
        assert (d2 / "Reports" / "BBB.NZ.duckdb").exists()

    def test_unreadable_csv_counts_as_failed(self, make_ticker, monkeypatch, capsys):
        d = make_ticker("BAD")
        (d / "Reports" / "BAD_Metrics.csv").mkdir()   # open() will raise
        assert run_main(monkeypatch, "BAD") == 0
        cap = capsys.readouterr()
        assert "ERROR BAD" in cap.err
        assert "0 ticker(s) loaded, 1 failed" in cap.out

    def test_csv_with_no_period_rows_writes_no_db(self, make_ticker, monkeypatch):
        d = load_syn(make_ticker, text="Period,Revenue\n,100\n")
        assert run_main(monkeypatch, "SYN") == 0
        assert not (d / "Reports" / "SYN.duckdb").exists()
