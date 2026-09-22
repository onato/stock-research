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


class TestGrocerSegmentAndOnlineMixPromotion:
    """A multi-banner grocer's segment split and online mix must reach the CSV.

    Ahold Delhaize's whole equity story outside the consolidated P&L is the
    US/Europe segment split (~57.5%/42.5% of revenue) and the online sales
    mix (EUR 10.3bn, 11.2% of FY2025 group revenue) -- both stored only in
    `kpis` and previously absent from PROMOTE_KPIS, so a dashboard could show
    consolidated revenue and margin only, never the segment or channel mix
    that management itself reports on. UnderlyingOperatingMargin is the
    company's own non-GAAP margin measure (management guides on it directly);
    comparable sales growth is the like-for-like volume/price measure a
    grocer discloses instead of same-store units; StoreCount and EmployeesFTE
    are the physical-footprint scale metrics.
    """

    def test_segment_and_online_kpis_are_promoted(self, patch_repo):
        make_db(patch_repo, "SYN",
                ["USSegmentRevenue", "EuropeSegmentRevenue",
                 "USSegmentOperatingIncome", "EuropeSegmentOperatingIncome",
                 "OnlineSales", "NetConsumerOnlineSales",
                 "UnderlyingOperatingMargin",
                 "ComparableSalesGrowthUS", "ComparableSalesGrowthEurope",
                 "StoreCount", "EmployeesFTE"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []


class TestStapledREITFundManagerSplitPromotion:
    """A stapled REIT + external funds manager's two-leg revenue split must
    reach the CSV.

    Stride Property Group (SPG.NZ) staples a property owner (SPL, earning
    gross rental income) to an external funds manager (SIML, earning
    management fee income on AUM it does not own). Consolidated Revenue
    hides which leg is growing -- SIML's fee income compounded 11.6%/yr
    FY2017-26 while SPL's rental income shrank on asset recycling -- and
    both names were absent from PROMOTE_KPIS, so a dashboard could only
    chart the blended total, never the two-leg mix that is the whole
    "REIT plus funds manager" investment thesis.
    """

    def test_two_leg_revenue_split_is_promoted(self, patch_repo):
        make_db(patch_repo, "SYN",
                ["GrossRentalIncome", "ManagementFeeIncome"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []


class TestFinanceReceivablesPromotion:
    """An integrated auto retailer's embedded loan book must reach the CSV.

    Turners Automotive Group (TRA.NZ) bundles Auto Retail with a directly
    originated consumer/commercial Finance book (Oxford Finance): gross
    Finance Receivables grew 27% YoY to $566m in FY2026 on a new public
    securitisation warehouse, the key driver behind the Finance segment.
    It was absent from PROMOTE_KPIS despite 7 populated periods (FY2021
    through H1 FY2026) sitting unreachable in the `kpis` table.
    """

    def test_finance_receivables_is_promoted(self, patch_repo):
        make_db(patch_repo, "SYN", ["FinanceReceivables"])
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        assert schema.promote_header("FinanceReceivables") == "FinanceReceivables"


class TestGeneralInsurerRatiosPromotion:
    """A general insurer's health metrics live in `kpis`, not core_metrics.

    Tower (TWR.NZ) reports combined operating ratio, BAU claims ratio and
    management expense ratio every period -- the standard health metrics
    for a general insurer -- plus gross written premium, net earned premium,
    insurance service result, underlying profit after tax, large event
    claims and its own diluted share count. None of the ten were reachable
    from the CSV, so H1 FY2026's claims-ratio normalisation and large-event
    spike (the story behind the profit decline) had no chart to sit in.
    """

    def test_insurer_kpis_are_promoted(self, patch_repo):
        names = [
            "GrossWrittenPremium", "CombinedOperatingRatio", "BAUClaimsRatio",
            "ManagementExpenseRatio", "NetEarnedPremium", "InsuranceServiceResult",
            "UnderlyingProfitAfterTax", "LargeEventClaims",
            "DividendPerShareDeclared", "SharesDiluted",
        ]
        make_db(patch_repo, "SYN", names)
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        for n in names:
            assert schema.promote_header(n) == n


class TestTradeSaaSUnitEconomicsPromotion:
    """A pre-profitability SaaS company's per-customer economics and
    retention must reach the CSV.

    TradeWindow (TWL.NZ) discloses segment-level average revenue per
    customer for shippers and freight forwarders (both compounding 20%+
    per period) and a customer retention rate that swung from 97% to 87%
    to 89% across recent periods -- the core value-driver and churn-risk
    story for a loss-making SaaS business. ShareIssuanceProceeds is the
    financing counterpart: a serial-dilution history is a named risk.
    None of the four were reachable from the CSV.
    """

    def test_saas_unit_economics_are_promoted(self, patch_repo):
        names = [
            "ARPCShippers", "ARPCFreightForwarders", "CustomerRetentionRate",
            "ShareIssuanceProceeds",
        ]
        make_db(patch_repo, "SYN", names)
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        for n in names:
            assert schema.promote_header(n) == n


class TestRetailerOnlineAndSameStoreSalesPromotion:
    """A bricks-and-mortar retailer's channel mix and like-for-like growth.

    The Warehouse Group (WHS.NZ) discloses online sales as a percentage of
    group sales and same-store (like-for-like) sales growth every interim
    period -- the two numbers that separate genuine demand growth from the
    store-count changes already visible in StoreCount. Both were absent
    from PROMOTE_KPIS, so a shrinking store footprint (88 -> 85 stores)
    had no accompanying like-for-like or online-mix chart to explain it.
    """

    def test_retail_unit_economics_are_promoted(self, patch_repo):
        names = ["OnlineSalesPct", "SameStoreSalesGrowth"]
        make_db(patch_repo, "SYN", names)
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        for n in names:
            assert schema.promote_header(n) == n


class TestGentailerPromotion:
    """A gentailer's generation output and retail customer accounts.

    AGL Energy (AGL.AX) discloses electricity generation volume (GWh) --
    declining as the coal fleet (Bayswater, Loy Yang A) retires toward its
    2030-2035 closures -- and total customer services (retail energy/telco
    accounts, in millions). Both were absent from PROMOTE_KPIS, so neither
    reached the CSV despite seven and eight populated periods respectively.
    """

    def test_gentailer_operating_kpis_are_promoted(self, patch_repo):
        names = ["GenerationVolume", "CustomerServices"]
        make_db(patch_repo, "SYN", names)
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []


class TestTestingLabSegmentAndUnderlyingPromotion:
    """A testing/inspection group's two-segment split and non-IFRS measures.

    ALS Limited (ALQ.AX) restructured from three reporting segments to two
    -- Commodities (~39% of FY2026 revenue) and Life Sciences (~61%) -- so
    the split is only available for FY2025/FY2026 and their half-years, not
    comparable further back. Management and the qualitative analysis both
    anchor on ALS's own non-IFRS "underlying" measures (e.g. "underlying
    NPAT +25.8% to A$381.2m" in FY26), distinct from statutory NPAT/EBIT/
    EBITDA/EPS. All six names were absent from PROMOTE_KPIS, so neither the
    segment mix nor the statutory-vs-underlying gap could reach the CSV.
    """

    def test_segment_and_underlying_kpis_are_promoted(self, patch_repo):
        names = ["SegmentRevenueCommodities", "SegmentRevenueLifeSciences",
                  "UnderlyingEBIT", "UnderlyingEBITDA", "UnderlyingEPS",
                  "UnderlyingNPAT"]
        make_db(patch_repo, "SYN", names)
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        for n in names:
            assert schema.promote_header(n) == n
        for n in names:
            assert schema.promote_header(n) == n


class TestPackagingSegmentAndAdjustedMeasuresPromotion:
    """A global packaging group's two-segment split and non-GAAP measures.

    Amcor plc (AMC.AX) reports two reporting segments -- Flexibles and
    Rigid Packaging -- and, after the Berry Global merger, GAAP results
    depressed by integration/transaction costs make its own non-GAAP
    Adjusted EBIT/EBITDA/Net Income/Free Cash Flow the figures management
    and analysts actually anchor run-rate profitability on. All six names
    were absent from PROMOTE_KPIS despite being populated in `kpis`.
    """

    def test_segment_and_adjusted_kpis_are_promoted(self, patch_repo):
        names = ["SegmentRevenueFlexibles", "SegmentRevenueRigidPackaging",
                  "AdjustedEBIT", "AdjustedEBITDA", "AdjustedNetIncome",
                  "AdjustedFreeCashFlow"]
        make_db(patch_repo, "SYN", names)
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        for n in names:
            assert schema.promote_header(n) == n


class TestSteelmakerSegmentAndDespatchPromotion:
    """A diversified steelmaker's five-segment EBIT and volume split.

    BlueScope Steel (BSL.AX) reports five segments -- Australian Steel
    Products, North Star BlueScope (US), New Zealand & Pacific Steel,
    Building Products Asia/NA and Coated Products Americas -- with North
    Star now the largest single EBIT contributor, only disclosed for
    FY2025/FY2026. Despatch volumes (kt) are the segments' own throughput
    measure. NetProfitAttributable excludes the ~10-15% non-controlling
    interest that NetIncome includes, and UnderlyingEBITROIC is BlueScope's
    own return-on-invested-capital measure. All eleven names were absent
    from PROMOTE_KPIS despite being populated in `kpis`.
    """

    def test_segment_and_despatch_kpis_are_promoted(self, patch_repo):
        names = ["SegmentEBIT_AustralianSteelProducts",
                 "SegmentEBIT_NorthStarBlueScope",
                 "SegmentEBIT_NewZealandPacificSteel",
                 "SegmentEBIT_BuildingProductsAsiaNA",
                 "SegmentEBIT_CoatedProductsAmericas",
                 "Despatches_AustralianSteelProducts",
                 "Despatches_NorthStarBlueScope",
                 "Despatches_BuildingProductsAsiaNA",
                 "Despatches_CoatedProductsAmericas",
                 "NetProfitAttributable", "UnderlyingEBITROIC"]
        make_db(patch_repo, "SYN", names)
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        for n in names:
            assert schema.promote_header(n) == n


class TestPoolingCapitalEfficiencyPromotion:
    """A pallet/crate/container pooling group's headline capital measures.

    Brambles Limited (BXB.AX) reports Return on Capital Invested (ROCI) as
    its own headline capital-efficiency KPI, measured against Average
    Capital Invested (the pooling equipment plus working capital base), and
    discloses the Irrecoverable Pooling Equipment Provision (IPEP) -- its
    distinctive pallet/crate loss charge -- as a separate expense line. All
    three names were absent from PROMOTE_KPIS despite being populated in
    `kpis`.
    """

    def test_pooling_kpis_are_promoted(self, patch_repo):
        names = ["ReturnOnCapitalInvested", "AverageCapitalInvested",
                  "IPEPExpense"]
        make_db(patch_repo, "SYN", names)
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        for n in names:
            assert schema.promote_header(n) == n


class TestPropertyDeveloperVolumePromotion:
    """A residential developer's settlement-based volume measures.

    Cedar Woods Properties (CWP.AX) recognises revenue on settlement, not
    accrual, so lot/unit sales, settlements and the presold-but-not-yet-
    settled dollar book (Presales) are the forward indicators management
    and the market watch. All three were absent from PROMOTE_KPIS despite
    being populated in `kpis`.
    """

    def test_developer_volume_kpis_are_promoted(self, patch_repo):
        names = ["NetSalesLots", "Presales", "Settlements"]
        make_db(patch_repo, "SYN", names)
        assert kpi_coverage.survey(patch_repo)["SYN"]["unmapped"] == []
        for n in names:
            assert schema.promote_header(n) == n
