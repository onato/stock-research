"""Tests for schema.py: header normalization and the units-scaling view.

The metrics_normalized semantics are the single most consequential unit rule
in the repo: canonical scale is millions of reporting currency, and unknown
units yield NULL — never an assumed scale (the SEK.NZ 1000x incident).
"""

from pytest import approx

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
        assert revenue_for(mem_db, "absolute")[0] == approx(0.0004)

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
