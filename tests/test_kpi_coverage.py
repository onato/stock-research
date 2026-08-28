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


class TestRetirementOperatorPromotion:
    """An ORA/DMF retirement operator's own earnings measure must reach the CSV.

    Summerset (and Ryman, Oceania) reports statutory NPAT dominated by IAS 40
    investment-property revaluation, so its valuation runs off `UnderlyingProfit`
    -- the company's own measure -- plus the ORA sales and portfolio counts.
    Those columns already existed in SUM.NZ_Metrics.csv with 16 periods of
    history, but they were absent from PROMOTE_KPIS, so adding H1-2026 wrote
    the row with every one of them blank: `carry_columns` only backfills
    periods the old CSV already had, which a newly added period never is.
    Same failure as NetRevenue above, on the metrics that drive the model.
    """

    def test_underlying_profit_and_ora_metrics_are_promoted(self, patch_repo):
        make_db(patch_repo, "SYN",
                ["UnderlyingProfit", "NPAT", "NetAssets",
                 "CareFeesAndVillageServices", "DeferredManagementFees",
                 "NewSalesORA", "ResalesORA", "TotalSalesORA",
                 "NewUnitsDelivered", "RetirementUnitsInPortfolio",
                 "CareUnitsInPortfolio", "DevelopmentMargin"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []

    def test_basic_eps_spelling_is_promoted(self, patch_repo):
        """`BasicEPS` is the spelling on the NZX retirement CSVs.

        PROMOTE_KPIS carries the canonical `EPS_Basic`; the kpis table stores
        the filing's own `BasicEPS`, which mapped to nothing and so came out
        blank on a newly added period.
        """
        make_db(patch_repo, "SYN", ["BasicEPS"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        assert schema.promote_header("BasicEPS") == "BasicEPS"


class TestDisaggregatedRevenueStreamPromotion:
    """A SaaS filer's revenue-by-stream split must reach the CSV.

    Serko discloses no cost of revenue, so there is no gross margin to chart
    and the only view of business mix is the IFRS 15 disaggregation note:
    travel platform booking, expense platform, supplier commissions, services
    and other. Supplier commissions is the Booking.com for Business line and
    ran 1.288 (FY2018) -> 73.387 (FY2026), taking it to ~61% of revenue --
    the concentration that is the whole investment thesis. Those names were
    absent from PROMOTE_KPIS, so the series existed in the `kpis` table and
    could not be charted: a dashboard could only show consolidated revenue,
    which hides the single fact that matters most.

    `TotalCashAndDeposits` is the same class of omission on the balance sheet.
    `CashAndEquivalents` is cash at bank only (14.146 at FY2026) against 40.0
    of short-term deposits sitting outside it, so the core column understates
    real liquidity by ~4x and a cash card built on it is simply wrong.
    """

    def test_revenue_streams_are_promoted(self, patch_repo):
        make_db(patch_repo, "SYN",
                ["TravelPlatformBookingRevenue", "ExpensePlatformRevenue",
                 "SupplierCommissionsRevenue", "ServicesRevenue",
                 "OtherRevenue"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []

    def test_liquidity_and_income_kpis_are_promoted(self, patch_repo):
        make_db(patch_repo, "SYN",
                ["TotalCashAndDeposits", "ShortTermDeposits", "TotalIncome",
                 "EBITDAFI", "NetTangibleAssetsPerShare",
                 "CapitalisedDevelopmentCosts"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []

    def test_total_cash_and_deposits_does_not_collide_with_core_cash(self):
        """It is a distinct concept from the `CashAndEquivalents` core column."""
        assert schema.promote_header("TotalCashAndDeposits") == "TotalCashAndDeposits"
        assert schema.promote_header("CashAndEquivalents") is None
