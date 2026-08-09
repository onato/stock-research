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

    def test_interim_fy_labels_sort_before_their_full_year(self):
        """`Q1 FY2024` is a quarter OF FY2024 and must precede it.

        The old token loop let the trailing `FY2024` overwrite the sub-rank
        the leading `Q1` had set, so every such interim tied with its own
        full year. That is why 21 committed CSVs were written with the year
        ahead of the quarters it contains.
        """
        periods = ["FY2024", "Q1 FY2024", "Q3 FY2024", "H1 FY2024",
                   "H1-FY2024"]
        assert sorted(periods, key=export_csv.sort_key) == [
            "Q1 FY2024", "H1 FY2024", "H1-FY2024", "Q3 FY2024", "FY2024"]

    def test_delegates_to_the_shared_parser(self):
        """One period grammar, not two that drift apart."""
        import periods as periods_mod
        for label in ("FY2024", "Q1 FY2024", "H1-2026", "9M 2023", "banana"):
            assert export_csv.sort_key(label) == periods_mod.sort_key(label)


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

    def test_refuses_when_more_rows_would_cost_populated_cells(
            self, make_ticker, monkeypatch):
        """Growing the row count is not proof the export is safe.

        Re-exporting UBER turned a 26-row CSV into 44 rows while silently
        dropping 325 populated cells -- CostOfRevenue, GrossProfit, TotalDebt
        -- whose columns exist in the CSV but were never adjudicated into
        core_metrics. LBTYA lost 108 the same way and PYPL 143. The row
        count went UP in every case, so the period-count guard passed.
        """
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2022", "FY2023", "FY2024"])
        out = d / "Reports" / "SYN_Metrics.csv"
        # One period, but carrying a value the table has no column populated for.
        out.write_text("Period,Revenue,CostOfRevenue\nFY2023,100,42\n")

        assert run_main(monkeypatch, "SYN") == 1
        assert "42" in out.read_text()

    def test_force_still_discards_them(self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2022", "FY2023", "FY2024"])
        out = d / "Reports" / "SYN_Metrics.csv"
        out.write_text("Period,Revenue,CostOfRevenue\nFY2023,100,42\n")

        assert run_main(monkeypatch, "SYN", "--force") == 0
        assert "42" not in out.read_text()

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
