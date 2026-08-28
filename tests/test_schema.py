"""Tests for schema.py: header normalization and the units-scaling view.

The metrics_normalized semantics are the single most consequential unit rule
in the repo: canonical scale is millions of reporting currency, and unknown
units yield NULL — never an assumed scale (the SEK.NZ 1000x incident).
"""

import pytest
import schema


class TestNormalize:
    def test_case_and_punctuation_insensitive(self):
        assert schema.normalize("Gross Margin") == "gross_margin"
        assert schema.normalize("gross_margin") == "gross_margin"
        assert schema.normalize("GrossMargin") == "gross_margin"

    def test_alias_spellings(self):
        assert schema.normalize("EPSDiluted") == "eps"
        assert schema.normalize("EPS_Basic") == "eps"
        assert schema.normalize("OCF") == "operating_cash_flow"
        assert schema.normalize("ShareBasedComp") == "stock_based_comp"

    def test_kpi_headers_are_none(self):
        assert schema.normalize("ARR") is None
        assert schema.normalize("SubscriptionRevenue") is None

    def test_non_string_input(self):
        assert schema.normalize(None) is None
        assert schema.normalize(123) is None


def revenue_for(con, units):
    con.execute("DELETE FROM core_metrics")
    con.execute(
        "INSERT INTO core_metrics (period, revenue, eps, gross_margin, units)"
        " VALUES ('FY2024', 400.0, 2.5, 40.0, ?)", [units])
    return con.execute(
        "SELECT revenue, eps, gross_margin FROM metrics_normalized").fetchone()


class TestMetricsNormalizedView:
    def test_known_units_scale_to_millions(self, mem_db):
        assert revenue_for(mem_db, "thousands")[0] == 0.4
        assert revenue_for(mem_db, "millions")[0] == 400.0
        assert revenue_for(mem_db, "billions")[0] == 400_000.0
        assert revenue_for(mem_db, "absolute")[0] == pytest.approx(0.0004)

    def test_units_matching_is_case_insensitive(self, mem_db):
        assert revenue_for(mem_db, "THOUSANDS")[0] == 0.4
        assert revenue_for(mem_db, "Millions")[0] == 400.0

    def test_unknown_units_yield_null_never_assumed(self, mem_db):
        # SEK.NZ files in thousands; defaulting unknown units to millions
        # read it as NZ$411bn of revenue for a ~NZ$400m company. A missing
        # row is obvious; a plausible wrong one is not.
        assert revenue_for(mem_db, "NZ$000")[0] is None
        assert revenue_for(mem_db, None)[0] is None

    def test_per_share_and_percentages_never_scaled(self, mem_db):
        row = revenue_for(mem_db, "thousands")
        assert row[1] == 2.5      # eps untouched
        assert row[2] == 40.0     # gross_margin untouched

    def test_share_counts_scale_with_units(self, mem_db):
        mem_db.execute("DELETE FROM core_metrics")
        mem_db.execute(
            "INSERT INTO core_metrics (period, shares_outstanding, units)"
            " VALUES ('FY2024', 500000.0, 'thousands')")
        assert mem_db.execute(
            "SELECT shares_outstanding FROM metrics_normalized").fetchone()[0] == 500.0


class TestFactsSchema:
    def test_facts_table_declares_currency(self, mem_db):
        # build_facts.py has emitted a currency per fact since the extractor
        # gained currency detection, but the canonical DDL lacked the column
        # and relied on a bolted-on ALTER at write time. New DBs must be
        # born with it.
        cols = {r[1] for r in mem_db.execute("PRAGMA table_info('facts')").fetchall()}
        assert "currency" in cols


class TestPromotableKpiVocabulary:
    """The `kpis` table is long-form and uncanonicalised; only a curated
    subset belongs in the cross-ticker CSV.

    Measured over the 171 ticker DBs: 871 distinct KPI names, 717 of which
    appear in exactly one ticker, and ~35% of all rows are DCF-component
    lines (InterestIncome on 67 tickers, ShareRepurchases 60, CashTaxesPaid
    58). Promoting everything would restore the 382-distinct-column drift
    this module exists to end.
    """

    def test_promote_vocabulary_has_no_core_header_collision(self):
        """A promoted header must never duplicate a core one.

        `StockBasedComp` sits in `kpis` for 4 tickers but is already a core
        column. Emitting both writes a duplicate CSV header, and
        csv.DictReader silently keeps only the last of them.
        """
        assert not set(schema.PROMOTE_KPIS.values()) & set(schema.CSV_HEADERS)

    def test_dcf_component_kpis_are_never_promoted(self):
        """DCF inputs reach the valuation via dcf_context.py, not the CSV."""
        assert not set(schema.PROMOTE_KPIS) & schema.DCF_COMPONENT_KPIS
        for name in ("InterestIncome", "CashTaxesPaid", "EquityAwardTaxes",
                     "ShareRepurchases", "DividendsPaid", "DeferredRevenue"):
            assert schema.promote_header(name) is None

    def test_promotes_a_business_kpi(self):
        assert schema.promote_header("ActiveCustomers") == "ActiveCustomers"
        assert schema.promote_header("MarketingExpense") == "MarketingExpense"

    def test_unknown_kpi_name_is_not_promoted(self):
        """Opt-in, not opt-out: an unrecognised name stays out of the CSV."""
        assert schema.promote_header("WaferShipments") is None

    def test_normalize_kpi_collapses_depreciation_spellings(self):
        """One concept, six spellings observed across the corpus."""
        names = ("Depreciation", "DandA", "DepreciationAmortisation",
                 "DepreciationAndAmortisation", "DepreciationAmortization",
                 "ebitda_da")
        assert len({schema.normalize_kpi(n) for n in names}) == 1

    def test_normalize_kpi_collapses_cash_taxes_spellings(self):
        assert schema.normalize_kpi("cash_taxes") == schema.normalize_kpi("CashTaxesPaid")

    def test_normalize_kpi_is_punctuation_and_case_insensitive(self):
        for spelling in ("ActiveCustomers", "active_customers", "Active Customers"):
            assert schema.normalize_kpi(spelling) == "ActiveCustomers"


class TestEnsureSchema:
    """A ticker DB created by an older schema must gain new core columns.

    `CREATE TABLE IF NOT EXISTS` is a no-op against an existing table, so a
    DB built before a column was added keeps the old shape forever. The
    INSERT then dies with a BinderException naming the missing column --
    which is exactly how ADBE's XBRL path failed and silently degraded to
    text extraction (24-column DB vs 28-column schema).
    """

    def test_adds_columns_missing_from_an_older_db(self, tmp_path):
        import duckdb
        db = tmp_path / "OLD.duckdb"
        con = duckdb.connect(str(db))
        # A DB as an earlier schema version left it: core columns only up to
        # dividend_per_share, without the *_continuing / *_before_significant set.
        old = [c for c in schema.CORE_COLUMNS
               if c[0] not in ("ebitda_before_significant", "revenue_continuing",
                               "ebitda_continuing_before_significant",
                               "ebit_continuing_before_significant")]
        cols = ",\n  ".join(f"{n} {t}" for n, t, _ in old)
        con.execute(f"CREATE TABLE core_metrics ({cols}, PRIMARY KEY (period))")
        con.execute("INSERT INTO core_metrics (period, revenue) VALUES ('FY2024', 100.0)")

        schema.ensure_schema(con)

        got = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='core_metrics'").fetchall()}
        assert set(schema.CORE_NAMES) <= got

    def test_migration_preserves_existing_rows(self, tmp_path):
        import duckdb
        db = tmp_path / "OLD2.duckdb"
        con = duckdb.connect(str(db))
        con.execute("CREATE TABLE core_metrics (period TEXT PRIMARY KEY, revenue DOUBLE)")
        con.execute("INSERT INTO core_metrics VALUES ('FY2024', 100.0)")

        schema.ensure_schema(con)

        assert con.execute(
            "SELECT revenue FROM core_metrics WHERE period='FY2024'").fetchone()[0] == 100.0

    def test_full_core_insert_succeeds_after_migration(self, tmp_path):
        """The regression itself: every core column must be writable."""
        import duckdb
        db = tmp_path / "OLD3.duckdb"
        con = duckdb.connect(str(db))
        con.execute("CREATE TABLE core_metrics (period TEXT PRIMARY KEY, revenue DOUBLE)")

        schema.ensure_schema(con)

        names = ",".join(schema.CORE_NAMES)
        holes = ",".join("?" for _ in schema.CORE_NAMES)
        vals: list[object] = []
        for n, t, _ in schema.CORE_COLUMNS:
            vals.append("FY2025" if n == "period" else ("USD" if t == "TEXT" else 1.0))
        con.execute(f"INSERT INTO core_metrics ({names}) VALUES ({holes})", vals)
        assert con.execute("SELECT count(*) FROM core_metrics").fetchone()[0] == 1

    def test_is_idempotent(self, tmp_path):
        import duckdb
        con = duckdb.connect(str(tmp_path / "NEW.duckdb"))
        schema.ensure_schema(con)
        schema.ensure_schema(con)
        got = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='core_metrics'").fetchall()}
        assert set(schema.CORE_NAMES) <= got

    def test_creates_the_normalized_view(self, tmp_path):
        import duckdb
        con = duckdb.connect(str(tmp_path / "V.duckdb"))
        schema.ensure_schema(con)
        con.execute("INSERT INTO core_metrics (period, revenue, units)"
                    " VALUES ('FY2024', 400.0, 'thousands')")
        assert con.execute(
            "SELECT revenue FROM metrics_normalized").fetchone()[0] == pytest.approx(0.4)
