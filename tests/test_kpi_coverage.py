"""Tests for kpi_coverage.py: which stored KPIs reach a dashboard."""

import duckdb
import kpi_coverage
import schema


def make_db(repo, ticker, names):
    d = repo / "research" / ticker / "Reports"
    d.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(d / f"{ticker}.duckdb"))
    con.execute(schema.create_sql())
    for n in names:
        con.execute("INSERT INTO kpis VALUES (?, ?, ?, ?)",
                    ["FY2024", n, 1.0, "units"])
    con.close()


class TestClassify:
    def test_splits_promoted_blocked_and_unmapped(self, patch_repo):
        make_db(patch_repo, "SYN",
                ["ActiveCustomers", "InterestIncome", "WaferShipments"])
        rows = kpi_coverage.survey(patch_repo)
        assert rows["SYN"]["promoted"] == ["ActiveCustomers"]
        assert rows["SYN"]["blocked"] == ["InterestIncome"]
        assert rows["SYN"]["unmapped"] == ["WaferShipments"]

    def test_alias_spellings_are_collapsed_before_counting(self, patch_repo):
        """DandA and Depreciation are one concept, reported once."""
        make_db(patch_repo, "SYN", ["DandA", "Depreciation"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["blocked"] == ["Depreciation"]

    def test_ticker_without_kpis_is_reported_empty(self, patch_repo):
        make_db(patch_repo, "SYN", [])
        r = kpi_coverage.survey(patch_repo)["SYN"]
        assert r == {"promoted": [], "blocked": [], "unmapped": []}


class TestNetRevenuePromotion:
    """A net-revenue reporter's top line must reach the CSV.

    Adyen (and any payments/marketplace filer that nets out interchange or
    merchant costs) has no gross `Revenue` line -- `NetRevenue` IS the top
    line. It was absent from PROMOTE_KPIS, so `export_csv` could not promote
    it into a newly added period and the column came out blank on every new
    row. `carry_columns` masked this for existing periods only.
    """

    def test_net_revenue_is_promoted(self, patch_repo):
        make_db(patch_repo, "SYN", ["NetRevenue"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["promoted"] == ["NetRevenue"]

    def test_disclosed_operating_kpis_are_promoted(self, patch_repo):
        """Headcount and the margin/EPS variants dashboards chart."""
        make_db(patch_repo, "SYN",
                ["EBITDAMargin", "EPS_Basic", "PayablesToMerchants", "FTE"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
