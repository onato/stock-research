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


def make_db(repo, ticker, periods, kpis=None):
    db = repo / "research" / ticker / "Reports" / f"{ticker}.duckdb"
    con = duckdb.connect(str(db))
    con.execute(schema.create_sql())
    for p in periods:
        con.execute(
            "INSERT INTO core_metrics (period, revenue, units) VALUES (?, ?, ?)",
            [p, 100.0, "millions"])
    for row in (kpis or []):
        con.execute("INSERT INTO kpis (period, name, value, unit) VALUES (?, ?, ?, ?)",
                    list(row))
    con.close()
    return db


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["export_csv.py", *argv])
    return export_csv.main()


class TestNonSchemaColumnsArePreserved:
    """The CSV is the committed system of record; the DB is a gitignored,
    rebuildable cache (CLAUDE.md). So a derived artifact must never subtract
    from its source: columns the canonical schema has no opinion about are
    carried through rather than dropped.

    58 of 126 committed CSVs carry such columns. For UBER, XYZ and PYPL they
    exist ONLY in the CSV -- XYZ's kpis table is empty outright -- and they
    are the figures the dashboards are built around (GrossBookings, MAPCs,
    Trips, SquareGPV, CashAppInflows, TPV, ActiveAccounts). Reading the kpis
    table instead of the CSV would silently destroy exactly those.
    """

    def test_unknown_column_survives_the_export(self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2023", "FY2024"])
        out = d / "Reports" / "SYN_Metrics.csv"
        out.write_text("Period,Revenue,GrossBookings\n"
                       "FY2023,100,31500\n"
                       "FY2024,100,37600\n")

        assert run_main(monkeypatch, "SYN") == 0
        with open(out, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert [r["GrossBookings"] for r in rows] == ["31500", "37600"]

    def test_carried_column_is_appended_after_the_schema_headers(
            self, make_ticker, monkeypatch):
        """Core columns keep their canonical order and spelling; carried
        ones follow, so dashboards reading by header still work."""
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2024"])
        out = d / "Reports" / "SYN_Metrics.csv"
        out.write_text("Period,Revenue,MAPCs\nFY2024,100,156\n")

        assert run_main(monkeypatch, "SYN") == 0
        with open(out, newline="") as fh:
            header = next(csv.reader(fh))
        assert header[:len(schema.CSV_HEADERS)] == schema.CSV_HEADERS
        assert header[len(schema.CSV_HEADERS):] == ["MAPCs"]

    def test_carried_values_follow_their_period_not_row_order(
            self, make_ticker, monkeypatch):
        """The export re-sorts oldest-first; a carried value must travel
        with its own period, not stay at its old row index."""
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2023", "FY2024"])
        out = d / "Reports" / "SYN_Metrics.csv"
        # deliberately newest-first on disk
        out.write_text("Period,Revenue,Trips\nFY2024,100,twenty\n"
                       "FY2023,100,ten\n")

        assert run_main(monkeypatch, "SYN") == 0
        with open(out, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert [(r["Period"], r["Trips"]) for r in rows] == [
            ("FY2023", "ten"), ("FY2024", "twenty")]

    def test_new_period_gets_a_blank_not_a_borrowed_value(
            self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2023", "FY2024"])
        out = d / "Reports" / "SYN_Metrics.csv"
        out.write_text("Period,Revenue,Trips\nFY2023,100,ten\n")

        assert run_main(monkeypatch, "SYN") == 0
        with open(out, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert [(r["Period"], r["Trips"]) for r in rows] == [
            ("FY2023", "ten"), ("FY2024", "")]

    def test_carrying_does_not_excuse_an_unpopulated_core_column(
            self, make_ticker, monkeypatch):
        """CostOfRevenue IS in the schema, so an empty table column is still
        real data loss and must still be refused -- carrying through is only
        for columns the schema does not model."""
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2023"])
        out = d / "Reports" / "SYN_Metrics.csv"
        out.write_text("Period,Revenue,CostOfRevenue,Trips\n"
                       "FY2023,100,42,ten\n")

        assert run_main(monkeypatch, "SYN") == 1
        assert "42" in out.read_text()

    def test_force_still_discards_carried_columns(self, make_ticker,
                                                  monkeypatch):
        """--force means "regenerate from the table alone"; it keeps its
        documented meaning rather than quietly becoming a merge."""
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2023"])
        out = d / "Reports" / "SYN_Metrics.csv"
        out.write_text("Period,Revenue,Trips\nFY2023,100,ten\n")

        assert run_main(monkeypatch, "SYN", "--force") == 0
        with open(out, newline="") as fh:
            header = next(csv.reader(fh))
        assert header == schema.CSV_HEADERS
        assert "ten" not in out.read_text()


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


class TestKpiPromotion:
    """Whitelisted business KPIs move from the `kpis` table into the CSV.

    Before this, `kpis` was write-mostly: 154 of 171 ticker DBs populate it
    and the only reader was dcf_context.py, so a metric like ActiveCustomers
    was extracted, stored, and then dropped -- the dashboard can only chart a
    CSV header. Promotion is the one-way ratchet from the gitignored cache
    into the committed system of record.
    """

    def test_whitelisted_kpi_becomes_a_csv_column(self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2024", "FY2025"], kpis=[
            ("FY2024", "ActiveCustomers", 1094000.0, "customers"),
            ("FY2025", "ActiveCustomers", 1274000.0, "customers"),
        ])
        assert run_main(monkeypatch, "SYN") == 0
        with open(d / "Reports/SYN_Metrics.csv", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert [r["ActiveCustomers"] for r in rows] == ["1094000.0", "1274000.0"]

    def test_kpi_period_joins_on_canonical_spelling(self, make_ticker, monkeypatch):
        """`H1 FY2020` in kpis and `H1-2020` in core_metrics are one period.

        periods.py is the single parser; a startswith("FY") style match would
        put this value on no row at all.
        """
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["H1-2020", "FY2020"], kpis=[
            ("H1 FY2020", "ActiveCustomers", 480000.0, "customers"),
        ])
        assert run_main(monkeypatch, "SYN") == 0
        with open(d / "Reports/SYN_Metrics.csv", newline="") as fh:
            rows = {r["Period"]: r for r in csv.DictReader(fh)}
        assert rows["H1-2020"]["ActiveCustomers"] == "480000.0"
        assert rows["FY2020"]["ActiveCustomers"] == ""

    def test_dcf_component_kpi_is_not_promoted(self, make_ticker, monkeypatch):
        """Owner-FCF inputs reach the DCF via dcf_context.py, not the CSV."""
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2024"], kpis=[
            ("FY2024", "InterestIncome", 0.4, "AUD millions"),
            ("FY2024", "CashTaxesPaid", 1.2, "AUD millions"),
        ])
        assert run_main(monkeypatch, "SYN") == 0
        with open(d / "Reports/SYN_Metrics.csv", newline="") as fh:
            hdr = next(csv.reader(fh))
        assert "InterestIncome" not in hdr
        assert "CashTaxesPaid" not in hdr

    def test_unmapped_kpi_is_not_promoted(self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2024"], kpis=[
            ("FY2024", "WaferShipments", 42.0, "units"),
        ])
        assert run_main(monkeypatch, "SYN") == 0
        with open(d / "Reports/SYN_Metrics.csv", newline="") as fh:
            hdr = next(csv.reader(fh))
        assert "WaferShipments" not in hdr

    def test_carried_column_survives_when_kpis_table_is_empty(
            self, make_ticker, monkeypatch):
        """The UBER/PYPL/XYZ regression, named for it.

        Those tickers hold their KPI columns ONLY in the committed CSV.
        Promotion must add to carry-through, never replace it.
        """
        d = make_ticker("SYN")
        out = d / "Reports/SYN_Metrics.csv"
        make_db(d.parent.parent, "SYN", ["FY2024"])
        out.write_text("Period,Revenue,GrossBookings\nFY2024,100.0,55.5\n")
        assert run_main(monkeypatch, "SYN") == 0
        with open(out, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["GrossBookings"] == "55.5"

    def test_carried_value_wins_over_a_conflicting_kpi_row(
            self, make_ticker, monkeypatch):
        """The CSV is the system of record; the DB is a rebuildable cache."""
        d = make_ticker("SYN")
        out = d / "Reports/SYN_Metrics.csv"
        make_db(d.parent.parent, "SYN", ["FY2024"], kpis=[
            ("FY2024", "ActiveCustomers", 1274000.0, "customers"),
        ])
        out.write_text("Period,Revenue,ActiveCustomers\nFY2024,100.0,999\n")
        assert run_main(monkeypatch, "SYN") == 0
        with open(out, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["ActiveCustomers"] == "999"

    def test_promoted_column_fills_only_blank_carried_cells(
            self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        out = d / "Reports/SYN_Metrics.csv"
        make_db(d.parent.parent, "SYN", ["FY2024", "FY2025"], kpis=[
            ("FY2024", "ActiveCustomers", 1094000.0, "customers"),
            ("FY2025", "ActiveCustomers", 1274000.0, "customers"),
        ])
        out.write_text("Period,Revenue,ActiveCustomers\n"
                       "FY2024,100.0,999\nFY2025,100.0,\n")
        assert run_main(monkeypatch, "SYN") == 0
        with open(out, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["ActiveCustomers"] == "999"          # CSV wins
        assert rows[1]["ActiveCustomers"] == "1274000.0"    # blank gets filled

    def test_force_keeps_promoted_columns(self, make_ticker, monkeypatch):
        """--force rebuilds from the DB, so it must not lose the DB's own KPIs.

        It still drops purely-carried columns (that is what --force means),
        but a promoted column is table-backed and therefore recoverable.
        """
        d = make_ticker("SYN")
        out = d / "Reports/SYN_Metrics.csv"
        make_db(d.parent.parent, "SYN", ["FY2024"], kpis=[
            ("FY2024", "ActiveCustomers", 1094000.0, "customers"),
        ])
        out.write_text("Period,Revenue,ActiveCustomers,GrossBookings\n"
                       "FY2024,100.0,999,55.5\n")
        assert run_main(monkeypatch, "SYN", "--force") == 0
        with open(out, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["ActiveCustomers"] == "1094000.0"   # rebuilt from the DB
        assert "GrossBookings" not in rows[0]              # carried, dropped

    def test_conflicting_duplicate_rows_produce_no_value(
            self, make_ticker, monkeypatch, capsys):
        """A silent pick between disagreeing values is the SEK.NZ class of bug:
        a missing cell is obvious, a plausible wrong one is not."""
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2024"], kpis=[
            ("FY2024", "ActiveCustomers", 1094000.0, "customers"),
            ("FY2024", "ActiveCustomers", 1234567.0, "customers"),
        ])
        assert run_main(monkeypatch, "SYN") == 0
        with open(d / "Reports/SYN_Metrics.csv", newline="") as fh:
            rows = list(csv.DictReader(fh))
        # No surviving value, so no column at all -- and a warning naming it.
        assert rows[0].get("ActiveCustomers", "") == ""
        assert "ActiveCustomers" in capsys.readouterr().err

    def test_agreeing_duplicate_rows_are_not_a_conflict(
            self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2024"], kpis=[
            ("FY2024", "ActiveCustomers", 1094000.0, "customers"),
            ("FY2024", "ActiveCustomers", 1094000.0, "customers"),
        ])
        assert run_main(monkeypatch, "SYN") == 0
        with open(d / "Reports/SYN_Metrics.csv", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["ActiveCustomers"] == "1094000.0"

    def test_kpi_values_are_not_rescaled(self, make_ticker, monkeypatch):
        """A headcount is not in millions. metrics_normalized deliberately
        does not touch kpis; the export must not either."""
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2024"], kpis=[
            ("FY2024", "ActiveCustomers", 1094000.0, "customers"),
        ])
        assert run_main(monkeypatch, "SYN") == 0
        with open(d / "Reports/SYN_Metrics.csv", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["ActiveCustomers"] == "1094000.0"

    def test_port_cargo_kpis_are_promoted(self, make_ticker, monkeypatch):
        """Port/terminal operators (SPN.NZ): tonnage is the operating driver,
        not just a financial-statement line, and is disclosed as such."""
        d = make_ticker("SYN")
        make_db(d.parent.parent, "SYN", ["FY2025", "FY2026"], kpis=[
            ("FY2025", "CargoVolumeTonnes", 3.553, "million tonnes"),
            ("FY2026", "CargoVolumeTonnes", 3.96, "million tonnes"),
            ("FY2026", "TiwaiVolumeTonnes", 0.942, "million tonnes"),
            ("FY2026", "BulkCargoVolumeTonnes", 2.36, "million tonnes"),
            ("FY2026", "ContainerCargoTonnes", 0.665, "million tonnes"),
            ("FY2026", "NormalisedNPAT", 16.14, "millions"),
        ])
        assert run_main(monkeypatch, "SYN") == 0
        with open(d / "Reports/SYN_Metrics.csv", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[1]["CargoVolumeTonnes"] == "3.96"
        assert rows[1]["TiwaiVolumeTonnes"] == "0.942"
        assert rows[1]["BulkCargoVolumeTonnes"] == "2.36"
        assert rows[1]["ContainerCargoTonnes"] == "0.665"
        assert rows[1]["NormalisedNPAT"] == "16.14"

    def test_promotion_is_idempotent(self, make_ticker, monkeypatch):
        d = make_ticker("SYN")
        out = d / "Reports/SYN_Metrics.csv"
        make_db(d.parent.parent, "SYN", ["FY2024"], kpis=[
            ("FY2024", "ActiveCustomers", 1094000.0, "customers"),
        ])
        assert run_main(monkeypatch, "SYN") == 0
        first = out.read_text()
        assert run_main(monkeypatch, "SYN") == 0
        assert out.read_text() == first
