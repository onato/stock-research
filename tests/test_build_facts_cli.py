"""End-to-end test of the build_facts.py CLI against a tmp repo."""

import sys

import build_facts as bf
import duckdb


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["build_facts.py", *argv])
    return bf.main()


STATEMENT = """ANNUAL REPORT
All amounts are in thousands ($000) unless otherwise stated.
The financial statements are presented in New Zealand dollars.

                                   2024          2023
Revenue                         263,527       267,805
Total assets                    150,321       140,654
"""


class TestCli:
    def test_writes_facts_with_currency(self, make_ticker, monkeypatch):
        d = make_ticker("SYN.NZ")
        (d / "Extracted" / "SYN.NZ_Annual_FY2024.txt").write_text(STATEMENT)

        assert run_main(monkeypatch, "SYN.NZ") == 0

        con = duckdb.connect(str(d / "Reports" / "SYN.NZ.duckdb"), read_only=True)
        rows = con.execute(
            "SELECT metric, period, value_raw, units_hint, currency, confidence"
            " FROM facts ORDER BY metric, confidence").fetchall()
        con.close()
        assert ("Revenue", "FY2024", 263527.0, "thousands", "NZD",
                "statement_line") in rows
        assert ("Revenue", "FY2023", 267805.0, "thousands", "NZD",
                "prior_year_column") in rows
        assert {r[0] for r in rows} == {"Revenue", "TotalAssets"}

    def test_rerun_replaces_not_appends(self, make_ticker, monkeypatch):
        d = make_ticker("SYN.NZ")
        (d / "Extracted" / "SYN.NZ_Annual_FY2024.txt").write_text(STATEMENT)
        run_main(monkeypatch, "SYN.NZ")
        run_main(monkeypatch, "SYN.NZ")
        con = duckdb.connect(str(d / "Reports" / "SYN.NZ.duckdb"), read_only=True)
        n = con.execute("SELECT count(*) FROM facts").fetchone()[0]
        con.close()
        assert n == 4

    def test_alter_shim_tolerates_old_dbs(self, make_ticker, monkeypatch):
        # A DB created before currency entered the DDL must still load.
        d = make_ticker("SYN.NZ")
        (d / "Extracted" / "SYN.NZ_Annual_FY2024.txt").write_text(STATEMENT)
        db = d / "Reports" / "SYN.NZ.duckdb"
        con = duckdb.connect(str(db))
        con.execute("CREATE TABLE facts (metric TEXT, period TEXT,"
                    " value_raw DOUBLE, units_hint TEXT, source_file TEXT,"
                    " line_no INTEGER, context TEXT, confidence TEXT)")
        con.close()
        assert run_main(monkeypatch, "SYN.NZ") == 0
        con = duckdb.connect(str(db), read_only=True)
        ccy = con.execute("SELECT DISTINCT currency FROM facts").fetchall()
        con.close()
        assert ccy == [("NZD",)]

    def test_missing_extracted_dir(self, patch_repo, monkeypatch, capsys):
        assert run_main(monkeypatch, "GHOST") == 1

    def test_no_args_usage(self, patch_repo, monkeypatch):
        assert run_main(monkeypatch) == 2


class TestFolderFiscalYearEnd:
    def test_interim_labelled_from_the_annuals_year_end(self, make_ticker, monkeypatch):
        d = make_ticker("JUN.NZ")
        (d / "Extracted" / "JUN.NZ_Annual_FY2025.txt").write_text(
            "ANNUAL REPORT\nFor the year ended 30 June 2025\nyear ended 30 June 2025\nyear ended 30 June 2025\n"
            "All amounts in thousands ($000)\nRevenue   10,000   9,000\n")
        (d / "Extracted" / "JUN.NZ_HalfYear_H1-2024.txt").write_text(
            "INTERIM REPORT\nFor the six months ended 31 December 2024\n"
            "six months ended 31 December 2024\nsix months ended 31 December 2024\nRevenue   5,500   5,000\n")
        assert run_main(monkeypatch, "JUN.NZ") == 0
        con = duckdb.connect(str(d / "Reports" / "JUN.NZ.duckdb"), read_only=True)
        rows = con.execute("SELECT source_file, period, value_raw FROM facts"
                           " WHERE metric='Revenue' ORDER BY source_file, value_raw").fetchall()
        con.close()
        assert ("JUN.NZ_HalfYear_H1-2024.txt", "H1 FY2025", 5500.0) in rows
        assert ("JUN.NZ_HalfYear_H1-2024.txt", "H1 FY2024", 5000.0) in rows
        assert ("JUN.NZ_Annual_FY2025.txt", "FY2025", 10000.0) in rows


class TestYearEndFromTheLatestAnnual:
    def test_a_changed_year_end_follows_the_newest_report(self, make_ticker, monkeypatch):
        # VUL.AX moved from a June to a December year end; the folder's older
        # annuals must not outvote the newest one when labelling new interims.
        d = make_ticker("VUL.AX")
        (d / "Extracted" / "VUL.AX_Annual_FY2022.txt").write_text(
            "year ended 30 June 2022\n" * 3 + "Revenue   1,000   900\n")
        (d / "Extracted" / "VUL.AX_Annual_FY2024.txt").write_text(
            "year ended 31 December 2024\n" * 3 + "Revenue   3,000   2,900\n")
        (d / "Extracted" / "VUL.AX_HalfYear_H1-2025.txt").write_text(
            "half-year ended 30 June 2025\n" * 3 + "Revenue   1,600   1,500\n")
        assert run_main(monkeypatch, "VUL.AX") == 0
        con = duckdb.connect(str(d / "Reports" / "VUL.AX.duckdb"), read_only=True)
        rows = con.execute("SELECT period FROM facts WHERE source_file LIKE '%H1-2025%'"
                           " AND confidence='statement_line'").fetchall()
        con.close()
        assert rows == [("H1 FY2025",)]
