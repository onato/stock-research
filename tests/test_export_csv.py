"""Tests for export_csv.py: period ordering and the anti-shrink guard."""

import csv
import sys

import duckdb

import export_csv
import schema


class TestSortKey:
    def test_full_year_sorts_after_its_parts(self):
        periods = ["FY2024", "H1-2024", "Q3-2024", "H2-2024"]
        assert sorted(periods, key=export_csv.sort_key) == [
            "H1-2024", "Q3-2024", "H2-2024", "FY2024"]

    def test_chronological_across_years(self):
        periods = ["FY2024", "H1-2025", "FY2023"]
        assert sorted(periods, key=export_csv.sort_key) == [
            "FY2023", "FY2024", "H1-2025"]

    def test_bare_year_token(self):
        assert export_csv.sort_key("2024")[:2] == (2024, 0)

    def test_empty_and_none(self):
        assert export_csv.sort_key(None) == (0, 0, "")
        assert export_csv.sort_key("") == (0, 0, "")


def make_db(repo, ticker, periods):
    db = repo / "research" / ticker / "Reports" / f"{ticker}.duckdb"
    con = duckdb.connect(str(db))
    con.execute(schema.create_sql())
    for p in periods:
        con.execute(
            "INSERT INTO core_metrics (period, revenue, units) VALUES (?, ?, ?)",
            [p, 100.0, "millions"])
    con.close()
    return db


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["export_csv.py", *argv])
    return export_csv.main()


class TestAntiShrinkGuard:
    def test_refuses_to_shrink_existing_csv(self, make_ticker, monkeypatch):
        # WISE.L regression: 18 CSV periods silently replaced by 5 annual
        # ones. A CSV with more periods than the table means the TABLE is
        # incomplete, not that the CSV is stale.
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2023", "FY2024"])
        out = d / "Reports" / "SYN_Metrics.csv"
        original = "Period\n" + "\n".join(
            f"H{h}-{y}" for y in (2023, 2024) for h in (1, 2)) + "\nFY2024\n"
        out.write_text(original)

        assert run_main(monkeypatch, "SYN") == 1
        assert out.read_text() == original

    def test_force_overwrites(self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2024", "FY2023"])
        out = d / "Reports" / "SYN_Metrics.csv"
        out.write_text("Period\n" + "P\n" * 9)

        assert run_main(monkeypatch, "SYN", "--force") == 0
        with open(out, newline="") as fh:
            rows = list(csv.reader(fh))
        assert rows[0] == schema.CSV_HEADERS
        # rows come out oldest-first regardless of insert order
        periods = [r[0] for r in rows[1:]]
        assert periods == ["FY2023", "FY2024"]

    def test_growing_export_is_allowed(self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2022", "FY2023", "FY2024"])
        out = d / "Reports" / "SYN_Metrics.csv"
        out.write_text("Period\nFY2023\n")

        assert run_main(monkeypatch, "SYN") == 0
        with open(out, newline="") as fh:
            assert sum(1 for _ in csv.reader(fh)) == 4

    def test_empty_table_exports_nothing(self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", [])
        assert run_main(monkeypatch, "SYN") == 1
        assert not (d / "Reports" / "SYN_Metrics.csv").exists()

    def test_missing_db(self, make_ticker, monkeypatch):
        make_ticker("SYN")
        assert run_main(monkeypatch, "SYN") == 1
