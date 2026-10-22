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

    def test_promotes_order_book(self):
        """Defense shipbuilder backlog (Austal Limited, ASB.AX): order book
        is the forward-revenue-visibility metric for a company that books
        multi-year contracts, distinct from in-period Revenue."""
        assert schema.promote_header("OrderBook") == "OrderBook"
        assert schema.promote_header("OrderBookWithOptions") == "OrderBookWithOptions"

    def test_promotes_li_ning_kpis(self):
        """Li Ning Company Limited (2331.HK): EPSDiluted sits alongside basic
        EPS; LeasePayments is the full lease cash payment (principal+
        interest), distinct from the blocked LeasePrincipalRepayment because
        the DCF treats lease cost as an operating expense, not debt service
        (no bank debt outstanding); SBCForfeitureReversal (large from
        FY2025: 167.9m FY2025, 210.4m H1 FY2026) is stored separately from
        StockBasedComp specifically so it is never netted into gross SBC."""
        assert schema.promote_header("EPSDiluted") == "EPSDiluted"
        assert schema.promote_header("LeasePayments") == "LeasePayments"
        assert (schema.promote_header("SBCForfeitureReversal")
                == "SBCForfeitureReversal")

    def test_promotes_tce_earnings(self):
        """Pacific Basin Shipping (2343.HK): Time-Charter Equivalent earnings
        is the standard dry-bulk shipping KPI -- net voyage revenue after
        voyage costs, reported per vessel-day against the BHSI/BSI indices --
        and is not derivable from Revenue, which also includes the separate
        'Operating Activity' chartered-in segment."""
        assert schema.promote_header("TCEEarnings") == "TCEEarnings"

    def test_promotes_china_jinmao_real_estate_kpis(self):
        """China Jinmao Holdings (0817.HK): LandAppreciationTaxPaid is a
        real-estate-specific levy on development-property disposals, and
        ProfitIncludingNCI vs attributable NetIncome is the gap driven by
        ~55% of total equity sitting in non-controlling interests plus the
        RMB15.2bn Sinochem HK perpetual instrument reclassified into equity
        attributable to owners from H1 FY2024. Neither is derivable from
        the existing CSV columns."""
        assert (schema.promote_header("LandAppreciationTaxPaid")
                == "LandAppreciationTaxPaid")
        assert (schema.promote_header("ProfitIncludingNCI")
                == "ProfitIncludingNCI")

    def test_promotes_hisense_home_appliances_kpis(self):
        """Hisense Home Appliances Group (0921.HK): the Hisense-Hitachi HVAC
        JV is only ~49-51% owned, so NetProfitInclMinority (total group net
        profit, ~RMB4.73bn FY2025) sits well above attributable NetIncome
        (~RMB3.19bn), with MinorityInterests (~RMB4.05bn FY2025) and
        MinorityDividendsPaid as the balance-sheet/cash-flow counterparts.
        TradingFinancialAssets (~RMB19-21bn of wealth-management/treasury
        products) is larger than operating cash and is the subject of the
        DCF's 35% trapped-capital discount. None is derivable from the
        existing CSV columns."""
        assert (schema.promote_header("NetProfitInclMinority")
                == "NetProfitInclMinority")
        assert (schema.promote_header("MinorityInterests")
                == "MinorityInterests")
        assert (schema.promote_header("MinorityDividendsPaid")
                == "MinorityDividendsPaid")
        assert (schema.promote_header("TradingFinancialAssets")
                == "TradingFinancialAssets")

    def test_promotes_sino_biopharmaceutical_nci_dividend(self):
        """Sino Biopharmaceutical (1177.HK): minority interests take roughly
        half of consolidated net profit via the NCI-heavy CT Tianqing
        subsidiary plus the LaNova Medicines/Hygieia acquisitions.
        DividendsPaidToNCI is the cash-flow counterpart to the
        already-promoted NonControllingInterests balance -- the actual cash
        leaving the group to minority shareholders, not derivable from the
        existing CSV columns."""
        assert (schema.promote_header("DividendsPaidToNCI")
                == "DividendsPaidToNCI")

    def test_promotes_revpar(self):
        """Hotel revenue per available room (Millennium & Copthorne Hotels
        New Zealand Limited, MCK.NZ) is a hotel-specific operating KPI, not
        derivable from the CSV's Revenue/Occupancy columns alone."""
        assert schema.promote_header("RevPAR") == "RevPAR"

    def test_promotes_sales_volume(self):
        """Beer sales volume in kilolitres (Tsingtao Brewery, 0168.HK) is the
        unit-economics metric behind the premiumization thesis -- volume vs
        price/mix -- and is not derivable from the CSV's Revenue column."""
        assert schema.promote_header("SalesVolumeKL") == "SalesVolumeKL"

    def test_promotes_container_shipping_yield(self):
        """Revenue per TEU (OOCL/Orient Overseas, 0316.HK) is the freight-rate
        yield behind a container carrier's revenue line -- volume
        (ContainerVolumeTEU) times this yield is the revenue story, and
        neither factor is derivable from the blended Revenue column alone."""
        assert schema.promote_header("RevenuePerTEU") == "RevenuePerTEU"

    def test_promotes_gold_reserves_and_resources(self):
        """Gold reserves/resources tonnes (Zijin Gold International, 2259.HK)
        are the resource-base metrics the investment thesis is built on --
        mine life and future production capacity -- and are not derivable
        from any income-statement or cash-flow column."""
        assert schema.promote_header("GoldReservesTonnes") == "GoldReservesTonnes"
        assert schema.promote_header("GoldResourcesTonnes") == "GoldResourcesTonnes"

    def test_unknown_kpi_name_is_not_promoted(self):
        """Opt-in, not opt-out: an unrecognised name stays out of the CSV."""
        assert schema.promote_header("WaferShipments") is None

    def test_promotes_share_award_scheme_purchases(self):
        """Chervon Holdings (2285.HK) discloses no SBC expense line, so the
        dcf-analyst uses on-market share purchases funding the employee
        share award scheme as the SBC-cost proxy. It is a real valuation
        driver, not derivable from the CSV's StockBasedComp column (which is
        blank for this ticker)."""
        assert (schema.promote_header("ShareAwardSchemePurchases")
                == "ShareAwardSchemePurchases")

    def test_promotes_franchisees(self):
        """Auntea Jenny (2589.HK): a franchise/wholesale tea-shop operator
        where franchisee count alongside StoreCount is a core growth KPI
        (5,455 FY2024 -> 6,974 FY2025 -> 8,014 H1 FY2026) -- not derivable
        from StoreCount, since the two can diverge (closures vs. onboarding)."""
        assert schema.promote_header("Franchisees") == "Franchisees"

    def test_promotes_insurance_revenue(self):
        """China Pacific Insurance Group (2601.HK): InsuranceRevenue is the
        IFRS 17-era top line (populated FY2022 onward, plus interim rows),
        reported alongside the pre-IFRS17 GrossWrittenPremiums -- the two
        never coexist for the same period, but each is the real top-line KPI
        for its own era and neither is derivable from the other."""
        assert schema.promote_header("InsuranceRevenue") == "InsuranceRevenue"

    def test_promotes_rnd_expense(self):
        """R&D expense (Lepu ScienTech Medical Technology, 2291.HK) is the
        spend behind the biodegradable-occluder and TAVR pipeline the
        qualitative thesis is built on, and is not derivable from any
        existing income-statement column (it sits inside OperatingIncome's
        deduction, not broken out on its own)."""
        assert schema.promote_header("RnDExpense") == "RnDExpense"

    def test_promotes_samsonite_brand_segment_channel_kpis(self):
        """Samsonite Group S.A. (1910.HK): a multi-brand, multi-region
        luggage maker where the brand mix (Samsonite/Tumi/American
        Tourister), regional segment mix (Asia/North America/Europe/Latin
        America) and wholesale-vs-DTC channel mix are each a distinct
        investor question -- none derivable from the blended Revenue
        column alone."""
        for name in (
            "BrandRevenueSamsonite", "BrandRevenueTumi",
            "BrandRevenueAmericanTourister",
            "SegmentRevenueAsia", "SegmentRevenueNorthAmerica",
            "SegmentRevenueEurope", "SegmentRevenueLatinAmerica",
            "WholesaleRevenue", "DTCRevenue",
        ):
            assert schema.promote_header(name) == name

    def test_promotes_picc_pc_underwriting_ratios(self):
        """PICC Property and Casualty (2328.HK): LossRatio and ExpenseRatio
        are the two components of CombinedOperatingRatio (already a
        promoted column) -- standard P&C underwriting-quality metrics, not
        derivable from CombinedOperatingRatio alone since a given COR can
        come from very different loss/expense splits. SolvencyRatio is the
        C-ROSS regulatory capital-adequacy ratio (~237% at H1 FY2026 per the
        qualitative analysis), a key risk indicator distinct from any
        balance-sheet column."""
        for name in ("LossRatio", "ExpenseRatio", "SolvencyRatio"):
            assert schema.promote_header(name) == name

    def test_promotes_sunny_optical_segment_revenue_kpis(self):
        """Sunny Optical Technology (2382.HK) restated its segment categories
        from FY2025 onward (Handset/VehicleRelated/ARVR -> Handset/Vehicle/
        XR/OtherProducts); HandsetRevenue, VehicleRevenue, XRRevenue and
        OtherProductsRevenue are the new-era names and carry the revenue-mix
        thesis (mature handset base vs. the vehicle/XR growth drivers), not
        derivable from total Revenue alone."""
        for name in (
            "HandsetRevenue", "VehicleRevenue", "XRRevenue",
            "OtherProductsRevenue",
        ):
            assert schema.promote_header(name) == name

    def test_promotes_capital_structure_for_soe_contractor(self):
        """China Railway Group (0390.HK) consolidates large PPP/infrastructure
        subsidiaries: TotalEquity and ProfitTotal include minority interests
        on top of the owners'-only ShareholdersEquity/NetIncome columns, and
        PerpetualNotes (equity-classified but economically debt-like) sit
        inside ShareholdersEquity. None is derivable from the existing CSV
        columns alone."""
        assert schema.promote_header("TotalEquity") == "TotalEquity"
        assert schema.promote_header("ProfitTotal") == "ProfitTotal"
        assert schema.promote_header("PerpetualNotes") == "PerpetualNotes"

    def test_promotes_crcc_perpetual_bonds(self):
        """China Railway Construction Corporation (1186.HK): total debt rose
        from RMB333bn (FY2023) to RMB728bn (H1 FY2026), and
        OtherEquityInstruments -- perpetual bonds classified in equity,
        RMB59.5bn FY2023 to RMB72.3bn H1 FY2026 -- are a debt-like claim
        sitting outside TotalDebt. Not derivable from existing columns."""
        assert (schema.promote_header("OtherEquityInstruments")
                == "OtherEquityInstruments")

    def test_promotes_concession_and_investment_property_capex(self):
        """Shanghai Industrial Holdings (0363.HK): toll-road concession
        purchases and investment-property capex are the infrastructure and
        property segments' reinvestment lines -- central to the bear/base/
        bull split on the 2027-2030 concession renewals, and not derivable
        from the blended CapEx column."""
        assert (schema.promote_header("InvestmentPropertyCapex")
                == "InvestmentPropertyCapex")
        assert (schema.promote_header("PurchaseOfOperatingConcessions")
                == "PurchaseOfOperatingConcessions")

    def test_promotes_investment_property_additions(self):
        """China Overseas Land & Investment (0688.HK): cash added to the
        held-for-rent investment-property book at fair value, distinct from
        InvestmentPropertyCapex (cost-basis capex spend) -- the lead
        indicator for whether the REIT-recyclable portfolio the bull case
        leans on is still growing or being run down."""
        assert (schema.promote_header("InvestmentPropertyAdditions")
                == "InvestmentPropertyAdditions")

    def test_promotes_rail_operating_metrics(self):
        """Railroad operating KPIs (Canadian National Railway, CNR.TO):
        Operating Ratio is the headline efficiency metric the whole rail
        industry is judged on, and Carloads/GrossTonMiles/RevenueTonMiles/
        FreightRevenuePerRTM are the volume and yield metrics behind
        revenue that a blended Revenue column cannot show."""
        for name in ("OperatingRatio", "AdjustedOperatingRatio", "Carloads",
                     "GrossTonMiles", "RevenueTonMiles",
                     "FreightRevenuePerRTM", "EmployeesEndOfPeriod"):
            assert schema.promote_header(name) == name

    def test_promotes_midstream_operating_metrics(self):
        """Pembina Pipeline (PPL.TO): TotalVolumes is pipeline/facility
        throughput in mboe/d -- a midstream operator's core operational
        driver, distinct from financial Revenue. AdjustedCashFlowFromOperations
        is Pembina's own disclosed non-GAAP operating cash flow measure."""
        assert schema.promote_header("TotalVolumes") == "TotalVolumes"
        assert schema.promote_header("AdjustedCashFlowFromOperations") == "AdjustedCashFlowFromOperations"

    def test_promotes_china_taiping_insurer_kpis(self):
        """China Taiping Insurance Holdings (0966.HK): GrossPremiumsWritten
        is the core insurer top-line KPI under the old IFRS 4 regime
        (populated FY2017-FY2021 only, before the IFRS 17 switch).
        NetInvestmentResult is the dcf-analyst's flagged single biggest swing
        driver of reported earnings (-HK$7.5bn FY2022 to +HK$14.6bn FY2025).
        RepoLiabilities (HK$106.7bn FY2025) is a material repo-funding line
        distinct from TotalDebt. None is derivable from the existing CSV
        columns."""
        assert (schema.promote_header("GrossPremiumsWritten")
                == "GrossPremiumsWritten")
        assert (schema.promote_header("NetInvestmentResult")
                == "NetInvestmentResult")
        assert (schema.promote_header("RepoLiabilities")
                == "RepoLiabilities")

    def test_promotes_new_china_life_gross_written_premiums(self):
        """New China Life Insurance (1336.HK): GrossWrittenPremiums is the
        core life-insurer top-line KPI (populated FY2016-FY2022 only, before
        the IFRS 17 transition replaced the summary-table disclosure). Same
        pre-IFRS17 break as 0966.HK's GrossPremiumsWritten, but a distinct
        name in this filer's own tables -- not derivable from Revenue."""
        assert (schema.promote_header("GrossWrittenPremiums")
                == "GrossWrittenPremiums")

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


class TestRedefinedKpiSeries:
    """A company that redefines a KPI needs two columns, not one series.

    Adobe reported Digital Media ARR through FY2025 ($19.20bn) and replaced
    it with the broader Total Adobe ARR from FY2026 ($26.06bn at Q1). Both
    are "ARR", so a single column splices them into a 36% jump where the
    company reported 10.9% growth -- the FY2025 10-K discloses both
    ($19.20bn and $25.20bn) precisely because they are different measures.

    The promotion whitelist is generic across tickers, so the qualified
    spellings are the generic mechanism: a prefix names WHICH series, and
    each lands in its own CSV column.
    """

    def test_qualified_arr_series_promote_separately(self):
        assert schema.promote_header("DigitalMediaARR") == "DigitalMediaARR"
        assert schema.promote_header("TotalAdobeARR") == "TotalAdobeARR"

    def test_qualified_series_do_not_collapse_into_plain_arr(self):
        """The whole point: they must not normalize to the same column."""
        names = {schema.normalize_kpi("DigitalMediaARR"),
                 schema.normalize_kpi("TotalAdobeARR"),
                 schema.normalize_kpi("ARR")}
        assert len(names) == 3

    def test_snake_case_from_the_kpis_table_promotes(self):
        """kpis rows are written snake_case; promotion must find them."""
        assert schema.promote_header("digital_media_arr") == "DigitalMediaARR"
        assert schema.promote_header("total_adobe_arr") == "TotalAdobeARR"

    def test_plain_arr_still_promotes(self):
        assert schema.promote_header("ARR") == "ARR"

    def test_promotes_rea_group_segment_revenue_split(self):
        """REA.AX: property advertising vs financial services (Mortgage
        Choice) is the core segment split, not noise."""
        assert (schema.promote_header("PropertyAdvertisingRevenue")
                == "PropertyAdvertisingRevenue")
        assert (schema.promote_header("FinancialServicesRevenue")
                == "FinancialServicesRevenue")

    def test_promotes_cathay_pacific_passenger_cargo_split(self):
        """0293.HK: passenger vs cargo revenue is the core segment split for
        an airline, not derivable from Revenue alone."""
        assert schema.promote_header("PassengerRevenue") == "PassengerRevenue"
        assert schema.promote_header("CargoRevenue") == "CargoRevenue"

    def test_promotes_qunabox_revenue_segment_split(self):
        """0917.HK: marketing services (~84% of FY2025 revenue) vs
        merchandise sales (smart retail, ~12%) vs other/IT services (~4%) is
        the core segment split for an AI interactive marketing terminal
        network, not derivable from Revenue alone."""
        assert (schema.promote_header("MarketingServicesRevenue")
                == "MarketingServicesRevenue")
        assert (schema.promote_header("MerchandiseSalesRevenue")
                == "MerchandiseSalesRevenue")
        assert (schema.promote_header("OtherServicesRevenue")
                == "OtherServicesRevenue")

    def test_promotes_mao_geping_selling_expense(self):
        """Mao Geping Cosmetics (1318.HK): selling/advertising expense is
        ~47% of revenue and the DCF's own margin-compression thesis turns on
        its ratio to revenue rising (45.2% -> 47.4% H1 FY2025 -> H1 FY2026),
        not derivable from the core OperatingIncome/EBITDA columns alone."""
        assert schema.promote_header("SellingExpense") == "SellingExpense"


class TestTotalAUMPromotion:
    """AMP.AX reports 'Total AUM' as its own named KPI, distinct from the
    generic AUM alias -- a wealth manager's platform/super fee revenue is
    driven off this exact figure, so it must land in its own CSV column
    rather than being dropped as unmapped."""

    def test_total_aum_promotes_to_its_own_column(self):
        assert schema.promote_header("TotalAUM") == "TotalAUM"

    def test_snake_case_total_aum_promotes(self):
        assert schema.promote_header("total_aum") == "TotalAUM"

    def test_total_aum_does_not_collapse_into_plain_aum(self):
        assert schema.normalize_kpi("TotalAUM") != schema.normalize_kpi("AUM")


class TestPeriodColumns:
    """core_metrics carries the parsed period beside the label, so SQL can
    order chronologically and pick annual rows without re-parsing strings
    (`ORDER BY period` is lexical: `9M 2021` < `FY2021` < `Q1 2021`)."""

    def test_create_sql_declares_the_derived_columns(self, mem_db):
        have = {r[0] for r in mem_db.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'core_metrics'").fetchall()}
        assert {"period_type", "fiscal_year", "months"} <= have
        # Derived, not core: the CSV export must not grow three columns.
        assert "period_type" not in schema.CORE_NAMES
        assert "PeriodType" not in schema.CSV_HEADERS

    def test_ensure_schema_migrates_a_legacy_table(self):
        import duckdb
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE core_metrics (period TEXT PRIMARY KEY, revenue DOUBLE)")
        con.execute("INSERT INTO core_metrics VALUES ('FY2024', 1.0)")
        schema.ensure_schema(con)
        have = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'core_metrics'").fetchall()}
        assert {"period_type", "fiscal_year", "months", "units"} <= have

    def test_backfill_fills_from_the_shared_period_parser(self, mem_db):
        for p in ("FY2024", "H1 FY2026", "9M 2021", "FY2017-15mo", "banana"):
            mem_db.execute("INSERT INTO core_metrics (period) VALUES (?)", [p])
        n = schema.backfill_period_columns(mem_db)
        assert n == 5
        got = {r[0]: r[1:] for r in mem_db.execute(
            "SELECT period, period_type, fiscal_year, months FROM core_metrics").fetchall()}
        assert got["FY2024"] == ("FY", 2024, 12)
        assert got["H1 FY2026"] == ("H1", 2026, 6)
        assert got["9M 2021"] == ("9M", 2021, 9)
        assert got["FY2017-15mo"] == ("OTHER", 2017, 15)
        assert got["banana"] == ("OTHER", None, None)

    def test_backfill_is_idempotent_and_only_touches_blank_rows(self, mem_db):
        mem_db.execute("INSERT INTO core_metrics (period) VALUES ('FY2024')")
        assert schema.backfill_period_columns(mem_db) == 1
        assert schema.backfill_period_columns(mem_db) == 0

    def test_view_exposes_period_columns_and_capex_abs(self, mem_db):
        mem_db.execute(
            "INSERT INTO core_metrics (period, capex, units) VALUES ('FY2024', -5.0, 'thousands')")
        schema.backfill_period_columns(mem_db)
        row = mem_db.execute(
            "SELECT period_type, fiscal_year, months, capex, capex_abs"
            " FROM metrics_normalized").fetchone()
        # capex keeps its stored sign; capex_abs is the sign-free outflow.
        assert row == ("FY", 2024, 12, -0.005, 0.005)


class TestProvenanceTables:
    def test_corrections_and_export_log_exist(self, mem_db):
        tables = {r[0] for r in mem_db.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        assert {"corrections", "export_log"} <= tables
        cols = {r[0] for r in mem_db.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'corrections'").fetchall()}
        assert {"ts", "target", "period", "col", "old_value", "new_value",
                "source", "actor", "op"} <= cols


class TestPromotedMinerKpis:
    def test_aisc_is_promotable(self):
        """All-in sustaining cost is the gold miner's comparability anchor;
        SMI.NZ's PFS figure was stored in kpis and never reached the CSV."""
        assert schema.promote_header("AISC") == "AISC"

    def test_royalty_streamer_kpis_promote(self):
        """Franco-Nevada (FNV.TO): gold equivalent ounces sold is the volume
        driver for a royalty/streaming company the way units shipped is for a
        manufacturer; realized gold price and cash cost per GEO are its
        commodity-exposure and margin metrics, all disclosed quarterly but
        absent from core_metrics and thus stuck in kpis."""
        assert schema.promote_header("GEOsSold") == "GEOsSold"
        assert schema.promote_header("AverageGoldPrice") == "AverageGoldPrice"
        assert schema.promote_header("CashCostsPerGEO") == "CashCostsPerGEO"

    def test_streaming_company_unit_economics_promote(self):
        """Wheaton Precious Metals (WPM.TO): a pure streamer with no mine to
        run, so GEO produced/sold is the volume driver, realized price per
        ounce is what it actually sold metal for, and cash cost per ounce is
        the near-fixed price its long-term purchase agreements lock in --
        the spread between the two (CashOperatingMarginPerGEO) is the whole
        economic engine. Metal-specific ounces/prices/costs break the blended
        GEO figures into gold and silver. Spelling differs from Franco-
        Nevada's (GEOSold not GEOsSold, CashCostPerGEO not CashCostsPerGEO) --
        these are genuinely distinct disclosed names, not typos."""
        for name in ("GEOProduced", "GEOSold", "GoldOuncesProduced",
                     "GoldOuncesSold", "SilverOuncesProduced",
                     "SilverOuncesSold", "RealizedGoldPrice",
                     "RealizedSilverPrice", "RealizedPricePerGEO",
                     "CashCostPerGEO", "GoldCashCostPerOz",
                     "SilverCashCostPerOz", "CashOperatingMarginPerGEO"):
            assert schema.promote_header(name) == name

    def test_conglomerate_segment_recurring_profit_kpis_promote(self):
        """Swire Pacific (0019.HK): a conglomerate whose reported net income
        is dominated by investment-property revaluation noise, so management
        and analysts track recurring/underlying profit by division instead --
        Property (Swire Properties), Beverages (Swire Coca-Cola), the Cathay
        Pacific equity-accounted group, wholly-owned HAECO plus other
        aviation-services interests, Trading & Industrial, and Head
        Office/Healthcare (typically negative: corporate costs net of the
        healthcare bolt-on). A single blended NetIncome or UnderlyingProfit
        column hides which division drives the mix shift."""
        for name in ("RecurringProfitProperty", "RecurringProfitBeverages",
                     "RecurringProfitCathayGroup",
                     "RecurringProfitHAECOAndOthers",
                     "RecurringProfitTradingIndustrial",
                     "RecurringProfitHeadOfficeHealthcare",
                     "RecurringUnderlyingProfit"):
            assert schema.promote_header(name) == name

    def test_hang_lung_revaluation_kpis_promote(self):
        """Hang Lung Properties (0101.HK): a commercial landlord whose
        statutory profit swings heavily with IAS 40 investment-property fair
        value revaluations, distinct from the InvestmentPropertyFairValueChange
        name used on Henderson Land (0012.HK). InvestmentPropertyRevaluation is
        the revaluation gain/loss itself; OperatingProfitAfterRevaluation is
        operating profit including it -- both are needed alongside the DCF's
        revaluation-excluded OperatingIncome to show the swing."""
        for name in ("InvestmentPropertyRevaluation",
                     "OperatingProfitAfterRevaluation"):
            assert schema.promote_header(name) == name

    def test_telecom_segment_and_arpu_kpis_promote(self):
        """Rogers Communications (RCI-B.TO): segment revenue across Wireless/
        Cable/Media is hidden by a single blended Revenue column, and
        BlendedARPU/BlendedABPU are non-comparable predecessor definitions of
        the already-promoted MobileARPU -- three series, not one continuous
        line. InternetNetAdds is the annual net change in broadband subs."""
        for name in ("SegmentRevenueWireless", "SegmentRevenueCable",
                     "SegmentRevenueMedia", "BlendedARPU", "BlendedABPU",
                     "InternetNetAdds"):
            assert schema.promote_header(name) == name

    def test_gas_utility_operating_kpis_promote(self):
        """Hong Kong and China Gas Company / Towngas (0003.HK): a gas
        utility's operating scale is customer count and gas volume sold, not
        the generic core_metrics columns. CustomersHK and
        MainlandCityGasCustomers (thousands) are the HK and mainland city-gas
        customer bases; TownGasSoldHK (million MJ) and MainlandGasSold
        (million m3) are the physical volumes sold in each market --
        different units because HK sells town gas (calorific-value basis)
        and the mainland JVs sell natural gas (volumetric basis).
        EmployeesHKCompany is headcount for the HK company only, a
        labour-productivity input distinct from any group-wide figure.
        DilutedEPS is the standard diluted per-share figure, disclosed
        alongside the already-promoted (core_metrics) EPS but on a different
        share-count basis. Only FY2023-FY2025 were extracted (earlier years
        were chart-image only), so all land in kpis with no core_metrics
        column."""
        for name in ("CustomersHK", "MainlandCityGasCustomers",
                     "MainlandGasSold", "TownGasSoldHK",
                     "EmployeesHKCompany", "DilutedEPS"):
            assert schema.promote_header(name) == name


class TestChinaEastEducationCashAndLeaseKpis:
    """China East Education Holdings (0667.HK): FVTPLFinancialAssets is
    non-principal-protected structured deposits and level-3 fund
    investments the dcf-analyst sums into net cash alongside
    CashAndEquivalents and TimeDeposits (30 Jun 2026: RMB1,488.0m of the
    RMB3,515.3m net-cash bridge) -- material enough that CashAndEquivalents
    alone understates the liquid balance by roughly 40%. LeasePrincipalRepaid
    is the IFRS-16 financing-cash-flow line the DCF's lease treatment (full
    cash payment, principal + interest, charged inside EBIT rather than
    treated as debt service) is built on, distinct from the already-charted
    OperatingCashFlow/CapEx lines. Both had multi-year history in `kpis`
    but no PROMOTE_KPIS entry."""

    def test_fvtpl_and_lease_principal_promote(self) -> None:
        assert (schema.promote_header("FVTPLFinancialAssets")
                == "FVTPLFinancialAssets")
        assert (schema.promote_header("LeasePrincipalRepaid")
                == "LeasePrincipalRepaid")

    def test_snake_case_variants_promote(self) -> None:
        assert (schema.promote_header("fvtpl_financial_assets")
                == "FVTPLFinancialAssets")
        assert (schema.promote_header("lease_principal_repaid")
                == "LeasePrincipalRepaid")


class TestHaitianFvtplInvestmentsKpi:
    """Haitian International Holdings (1882.HK): FVTPLInvestments is the
    wealth-management-product treasury line (bank/trust/fund products at fair
    value through P&L) alongside the already-promoted TermDeposits --
    ~RMB10.2bn at H1 FY2026, roughly a quarter of the DCF's weighted
    intrinsic value, valued separately in the equity_bridge at fair value
    less a 10% haircut. Populated FY2018-FY2025 in `kpis` but absent from
    PROMOTE_KPIS, so the dashboard could not chart it."""

    def test_fvtpl_investments_promotes(self) -> None:
        assert (schema.promote_header("FVTPLInvestments")
                == "FVTPLInvestments")

    def test_snake_case_variant_promotes(self) -> None:
        assert (schema.promote_header("fvtpl_investments")
                == "FVTPLInvestments")


class TestDpcDashLeaseInterestKpi:
    """DPC Dash (1405.HK): LeaseInterestPaid is the IFRS-16 financing-cash-
    flow interest line alongside the already-promoted LeasePrincipalRepaid --
    together they are the full lease cash burden the DCF deducts from
    reported FCF to get Owner FCF (FY2025: 386.9 - 370.2 principal - 74.0
    interest - 46.0 SBC - 17.3 interest income = -120.6m, negative every
    year FY2022-25). Populated FY2022-25 plus four half-years in `kpis` but
    absent from PROMOTE_KPIS, so the dashboard could not chart it."""

    def test_lease_interest_promotes(self) -> None:
        assert (schema.promote_header("LeaseInterestPaid")
                == "LeaseInterestPaid")

    def test_snake_case_variant_promotes(self) -> None:
        assert (schema.promote_header("lease_interest_paid")
                == "LeaseInterestPaid")


class TestTechtronicCapitalisedDevelopmentKpi:
    """Techtronic Industries (0669.HK): CapitalisedDevelopment is R&D spend
    capitalised as an intangible asset rather than expensed -- $135.6m in
    FY2018 growing to $300-400m/yr by FY2022-FY2025, folded by the
    financial-parser agent into the CapEx figure used for FCF. 11 periods
    populated in `kpis` (FY2018-FY2026 plus half-years) but absent from
    PROMOTE_KPIS, so the dashboard could not chart it separately from
    cash CapEx."""

    def test_capitalised_development_promotes(self) -> None:
        assert (schema.promote_header("CapitalisedDevelopment")
                == "CapitalisedDevelopment")

    def test_snake_case_variant_promotes(self) -> None:
        assert (schema.promote_header("capitalised_development")
                == "CapitalisedDevelopment")


class TestPromotedBankBalanceSheetKpis:
    """ANZ Group Holdings (ANZ.AX) disclosures the NIM/CET1/ROE bank-quality
    columns don't cover: the cash-earnings measure management guides to, the
    credit cycle (impairment charge), balance-sheet growth (deposits and net
    loans), and the cost line the cost-to-income story runs off. All five sat
    in `kpis` unmapped to any CSV column."""

    def test_bank_balance_sheet_kpis_promote(self):
        assert schema.promote_header("CashProfit") == "CashProfit"
        assert schema.promote_header("CreditImpairmentCharge") == "CreditImpairmentCharge"
        assert schema.promote_header("CustomerDeposits") == "CustomerDeposits"
        assert schema.promote_header("NetLoansAndAdvances") == "NetLoansAndAdvances"
        assert schema.promote_header("OperatingExpenses") == "OperatingExpenses"


class TestHongqiaoEffectiveCashTaxRateKpi:
    """China Hongqiao Group Limited (1378.HK): EffectiveCashTaxRate (cash
    taxes paid / profit before tax) swings 10.6%-48.8% across FY2018-25 --
    an aluminium smelter with provincial tax incentives and swing items
    that ProfitBeforeTax and CashTaxesPaid alone do not explain. 12 periods
    populated in `kpis` (FY2018-FY2025 plus four half-years) but absent
    from PROMOTE_KPIS, so the dashboard could not chart it."""

    def test_effective_cash_tax_rate_promotes(self) -> None:
        assert (schema.promote_header("EffectiveCashTaxRate")
                == "EffectiveCashTaxRate")

    def test_snake_case_variant_promotes(self) -> None:
        assert (schema.promote_header("effective_cash_tax_rate")
                == "EffectiveCashTaxRate")


class TestTencentMusicSegmentRevenueKpis:
    """Tencent Music Entertainment Group (1698.HK): OnlineMusicRevenue grew
    ~9.3bn -> ~26.7bn RMB FY2021-FY2025 while SocialEntertainmentRevenue
    (live-streaming/virtual gifting) shrank ~19.8bn -> ~6.2bn under Chinese
    regulatory pressure -- the two offsetting segment trends are why total
    Revenue looked flat FY2022-FY2024 despite a fundamental business-mix
    shift. Populated in `kpis` FY2020-FY2025 but absent from PROMOTE_KPIS,
    so the dashboard could not chart the segment split separately from
    Revenue."""

    def test_online_music_revenue_promotes(self) -> None:
        assert (schema.promote_header("OnlineMusicRevenue")
                == "OnlineMusicRevenue")

    def test_social_entertainment_revenue_promotes(self) -> None:
        assert (schema.promote_header("SocialEntertainmentRevenue")
                == "SocialEntertainmentRevenue")

    def test_snake_case_variants_promote(self) -> None:
        assert (schema.promote_header("online_music_revenue")
                == "OnlineMusicRevenue")
        assert (schema.promote_header("social_entertainment_revenue")
                == "SocialEntertainmentRevenue")


class TestCnoocRealizedPriceAndVolumeKpis:
    """CNOOC Limited (0883.HK): disclosed realized oil/gas prices
    (US$/barrel, US$/mcf) and oil-and-gas sales volume (mmboe) are the
    standard E&P investor metrics -- realized price versus benchmark and
    volume growth -- and are not derivable from Revenue, which also
    includes trading. Full annual history (FY2019-FY2025) and both half
    years sat in `kpis` unmapped to any CSV column."""

    def test_realized_price_and_volume_kpis_promote(self) -> None:
        assert schema.promote_header("RealizedOilPrice") == "RealizedOilPrice"
        assert schema.promote_header("RealizedGasPrice") == "RealizedGasPrice"
        assert schema.promote_header("SalesVolume") == "SalesVolume"

    def test_snake_case_variants_promote(self) -> None:
        assert schema.promote_header("realized_oil_price") == "RealizedOilPrice"
        assert schema.promote_header("realized_gas_price") == "RealizedGasPrice"
        assert schema.promote_header("sales_volume") == "SalesVolume"

    def test_snake_case_bank_balance_sheet_kpis_promote(self):
        assert schema.promote_header("credit_impairment_charge") == "CreditImpairmentCharge"
        assert schema.promote_header("customer_deposits") == "CustomerDeposits"
        assert schema.promote_header("net_loans_and_advances") == "NetLoansAndAdvances"

    def test_total_common_equity_promotes(self):
        """Bank of Nova Scotia (BNS): ShareholdersEquity/core_metrics includes
        preferred shares and other equity instruments, while TotalCommonEquity
        is equity attributable to common shareholders only -- the correct
        basis for book-value-per-share and ROE-on-common, and it sat in kpis
        unmapped to any CSV column."""
        assert schema.promote_header("TotalCommonEquity") == "TotalCommonEquity"
        assert schema.promote_header("total_common_equity") == "TotalCommonEquity"

    def test_efficiency_ratio_and_pre_pcl_profit_promote(self):
        """CIBC (CM): EfficiencyRatio (non-interest expense / revenue) and
        PrePCLProfit (pre-provision, pre-credit-loss profit) are the two core
        bank profitability KPIs management guides to each quarter, neither
        derivable from the generic core_metrics columns, and both sat in
        kpis unmapped to any CSV column."""
        assert schema.promote_header("EfficiencyRatio") == "EfficiencyRatio"
        assert schema.promote_header("efficiency_ratio") == "EfficiencyRatio"
        assert schema.promote_header("PrePCLProfit") == "PrePCLProfit"
        assert schema.promote_header("pre_pcl_profit") == "PrePCLProfit"


class TestPromotedBoqLoanBookAndCashEpsKpis:
    """Bank of Queensland (BOQ.AX): the gross loan book and total deposit
    base -- the funding/lending pair a bank's balance sheet runs on, distinct
    from the CustomerDeposits/NetLoansAndAdvances columns other banks already
    populate -- plus cash EPS, the per-share measure management and the DCF
    lean on given the FY2025 statutory/cash NPAT divergence (goodwill
    impairment). All three sat in `kpis` unmapped to any CSV column."""

    def test_boq_loan_book_and_cash_eps_kpis_promote(self):
        assert schema.promote_header("GrossLoansAndAdvances") == "GrossLoansAndAdvances"
        assert schema.promote_header("TotalDeposits") == "TotalDeposits"
        assert schema.promote_header("CashEPS") == "CashEPS"

    def test_snake_case_boq_loan_book_and_cash_eps_kpis_promote(self):
        assert schema.promote_header("gross_loans_and_advances") == "GrossLoansAndAdvances"
        assert schema.promote_header("total_deposits") == "TotalDeposits"
        assert schema.promote_header("cash_eps") == "CashEPS"


class TestPromotedAnsellSegmentKpis:
    """Ansell Limited (ANN.AX) reports two segments -- Healthcare and
    Industrial -- that grow revenue and expand EBIT margin at different
    rates; a blended Revenue/EBIT column hides which segment drives the
    investment case, so both segments' revenue, EBIT, EBIT margin and
    organic growth need their own CSV columns."""

    def test_segment_kpis_promote(self):
        assert schema.promote_header("HealthcareRevenue") == "HealthcareRevenue"
        assert schema.promote_header("IndustrialRevenue") == "IndustrialRevenue"
        assert schema.promote_header("HealthcareEBIT") == "HealthcareEBIT"
        assert schema.promote_header("IndustrialEBIT") == "IndustrialEBIT"
        assert schema.promote_header("HealthcareEBITMargin") == "HealthcareEBITMargin"
        assert schema.promote_header("IndustrialEBITMargin") == "IndustrialEBITMargin"
        assert schema.promote_header("HealthcareOrganicGrowth") == "HealthcareOrganicGrowth"
        assert schema.promote_header("IndustrialOrganicGrowth") == "IndustrialOrganicGrowth"

    def test_group_level_kpis_promote(self):
        assert schema.promote_header("OrganicRevenueGrowth") == "OrganicRevenueGrowth"
        assert schema.promote_header("AdjustedEBITMargin") == "AdjustedEBITMargin"
        assert schema.promote_header("AdjustedEPS") == "AdjustedEPS"

    def test_snake_case_segment_kpis_promote(self):
        assert schema.promote_header("healthcare_revenue") == "HealthcareRevenue"
        assert schema.promote_header("industrial_ebit_margin") == "IndustrialEBITMargin"


class TestPromotedMoneyTransferVolumeAndHoldingsKpis:
    """Wise (WISE.L): cross-border volume is the volume series behind the
    take rate, and customer holdings (on-balance-sheet balances plus assets
    under custody) drive the interest income that is ~a third of net
    revenue. Both had full history in `kpis` but no CSV column."""

    def test_wise_volume_and_holdings_kpis_promote(self):
        for name in ("CrossBorderVolume", "CustomerHoldings",
                     "CustomerBalances", "AssetsUnderCustody"):
            assert schema.promote_header(name) == name


class TestPromotedBiopharmaLicensingKpis:
    """HUTCHMED (0013.HK): revenue mixes lumpy licensing/milestone income
    (Takeda's FRUZAQLA upfront drove a one-off FY2023 spike) with steadily
    compounding royalties from ex-China marketing partners (Takeda,
    AstraZeneca, Eli Lilly) -- a blended Revenue column hides that mix
    shift. R&D spend more than halved FY2022->FY2025 as the pipeline
    matured past its most spend-intensive trials, which is the other half
    of the investment case a generic operating template misses."""

    def test_licensing_and_royalty_kpis_promote(self):
        assert schema.promote_header("LicensingRevenue") == "LicensingRevenue"
        assert schema.promote_header("RoyaltyRevenue") == "RoyaltyRevenue"

    def test_rnd_kpi_promotes(self):
        assert schema.promote_header("ResearchAndDevelopment") == "ResearchAndDevelopment"

    def test_snake_case_variants_promote(self):
        assert schema.promote_header("licensing_revenue") == "LicensingRevenue"
        assert schema.promote_header("royalty_revenue") == "RoyaltyRevenue"
        assert schema.promote_header("research_and_development") == "ResearchAndDevelopment"


class TestChinaRuyiFairValueKpis:
    """China Ruyi Holdings (0136.HK): reported operating profit is
    dominated by 'Other gains/(losses) -- net', fair-value marks on a large
    investment book (FY2024 loss RMB1,222.0m, FY2025 gain RMB1,281.7m,
    swinging reported NetIncome from -190.5m to +1,796.6m) that the DCF
    strips out entirely to reach the underlying film/TV/streaming/games
    operating economics. AdjustedNetProfit is the company's own non-GAAP
    measure excluding that swing; FilmRightsAmortisation is the content
    amortisation cost the DCF explicitly does NOT add back. All three had
    full history in `kpis` but no CSV column."""

    def test_other_gains_and_adjusted_profit_kpis_promote(self):
        assert schema.promote_header("OtherGainsNet") == "OtherGainsNet"
        assert schema.promote_header("AdjustedNetProfit") == "AdjustedNetProfit"
        assert schema.promote_header("FilmRightsAmortisation") == "FilmRightsAmortisation"

    def test_snake_case_variants_promote(self):
        assert schema.promote_header("other_gains_net") == "OtherGainsNet"
        assert schema.promote_header("adjusted_net_profit") == "AdjustedNetProfit"
        assert schema.promote_header("film_rights_amortisation") == "FilmRightsAmortisation"


class TestSinofertTimeDepositsKpi:
    """Sinofert Holdings (0297.HK): time deposits (FY2025 RMB3,269.0m) sit
    alongside cash and equivalents on the balance sheet and are comparable
    in size to that line -- the dcf-analyst's net-cash bridge explicitly
    sums cash + time deposits (H1 FY2026: 2,722.4 + 2,972.7 = 5,695.1) to
    reach the DCF's net_debt figure. CashAndEquivalents alone understates
    the liquid balance by roughly half; this KPI had full history in
    `kpis` but no CSV column."""

    def test_time_deposits_kpi_promotes(self):
        assert schema.promote_header("TimeDeposits") == "TimeDeposits"

    def test_snake_case_variant_promotes(self):
        assert schema.promote_header("time_deposits") == "TimeDeposits"


class TestChinaMedicalSystemLicensingAndNCIKpis:
    """China Medical System Holdings (0867.HK): IntangibleAssetPurchases is
    cash paid to acquire in-licensed drug product rights -- the dcf-analyst
    treats it as mandatory reinvestment, deducted from owner FCF every year,
    because the growth model depends on continuously buying licences
    (RMB423-473m/yr in FY2024-25, RMB468m in H1 FY2026 alone). It had full
    history back to FY2017 in `kpis` but no CSV column, making that spend
    invisible on the dashboard. NetIncomeIncludingNCI is profit including
    non-controlling interests, alongside the already-charted NetIncome
    (attributable to owners)."""

    def test_licensing_and_nci_kpis_promote(self):
        assert (schema.promote_header("IntangibleAssetPurchases")
                == "IntangibleAssetPurchases")
        assert (schema.promote_header("NetIncomeIncludingNCI")
                == "NetIncomeIncludingNCI")

    def test_snake_case_variants_promote(self):
        assert (schema.promote_header("intangible_asset_purchases")
                == "IntangibleAssetPurchases")
        assert (schema.promote_header("net_income_including_nci")
                == "NetIncomeIncludingNCI")


class TestChinaMobileTelecomServicesRevenueKpi:
    """China Mobile Limited (0941.HK): TelecomServicesRevenue is the
    core mobile/broadband/IoT subscription line, stored separately from
    total Revenue because Revenue also includes product/terminal sales
    and the fast-growing computing/AIDC/AI services segments. The
    investment thesis centers on this mix shift -- core telecom services
    growth has decelerated and gone negative YoY in H1 FY2026 (452,663m
    vs 466,989m) while total Revenue keeps growing on computing/AI -- so
    a single blended Revenue column hides the story. Full annual and
    half-year history back to FY2018 sat in `kpis` with no CSV column."""

    def test_telecom_services_revenue_promotes(self):
        assert (schema.promote_header("TelecomServicesRevenue")
                == "TelecomServicesRevenue")

    def test_snake_case_variant_promotes(self):
        assert (schema.promote_header("telecom_services_revenue")
                == "TelecomServicesRevenue")


class TestBrillianceChinaJvKpis:
    """Brilliance China Automotive Holdings (1114.HK): a holding company
    whose consolidated Revenue is tiny -- the overwhelming majority of net
    income is ShareOfAssociatesAndJVResults, the equity-accounted share of
    results from the 25%-owned BMW Brilliance Automotive joint venture.
    ProfitInclNCI is total profit including non-controlling interests, the
    same attributable-vs-total gap as ProfitIncludingNCI/NetProfitInclMinority
    above but this filing's own disclosed line, not an alias of either."""

    def test_share_of_associates_and_jv_results_promotes(self):
        assert (schema.promote_header("ShareOfAssociatesAndJVResults")
                == "ShareOfAssociatesAndJVResults")

    def test_profit_incl_nci_promotes(self):
        assert schema.promote_header("ProfitInclNCI") == "ProfitInclNCI"


class TestAiaGroupEmbeddedValueKpis:
    def test_insurer_ev_and_investment_income_promote(self):
        # AIA Group (1299.HK): EVEquity is disclosed Embedded Value, the
        # insurer valuation anchor the dcf-analyst cross-checks against --
        # distinct from statutory ShareholdersEquity. InterestRevenueOn-
        # FinancialAssets is interest earned on the investment portfolio,
        # kept separate from a stripped non-operating interest line.
        assert schema.promote_header("EVEquity") == "EVEquity"
        assert (schema.promote_header("InterestRevenueOnFinancialAssets")
                == "InterestRevenueOnFinancialAssets")
        assert schema.promote_header("ev_equity") == "EVEquity"
        assert (schema.promote_header("interest_revenue_on_financial_assets")
                == "InterestRevenueOnFinancialAssets")


class TestSunevisionCashGeneratedFromOperationsKpi:
    """SUNeVision Holdings (1686.HK): the debt-funded data-centre build-out
    makes InterestPaid a growing drag between operating cash and the
    post-interest OperatingCashFlow column (~HK$10m FY2017 to >HK$800m
    FY2025). CashGeneratedFromOperations is the pre-interest, pre-tax cash
    line that makes that coverage visible; 12 periods populated in `kpis`
    but absent from PROMOTE_KPIS, so the dashboard could not chart it."""

    def test_cash_generated_from_operations_promotes(self) -> None:
        assert (schema.promote_header("CashGeneratedFromOperations")
                == "CashGeneratedFromOperations")

    def test_snake_case_variant_promotes(self) -> None:
        assert (schema.promote_header("cash_generated_from_operations")
                == "CashGeneratedFromOperations")


class TestTimeInterconnectDebtAndPlacingKpis:
    """Time Interconnect Technology (1729.HK): Luxshare Precision Industry is
    both the controlling shareholder and, via LoansFromRelatedCompanies, a
    lender -- that split of TotalDebt into bank vs related-party is the
    leverage/dependency story, not derivable from TotalDebt alone. The H1
    FY2026 placings (SharePlacingProceeds, SharesIssuedPeriodEnd) lifted the
    share count ~21% in one half-year. All four populated in `kpis` but
    absent from PROMOTE_KPIS, so the dashboard could not chart them."""

    def test_bank_borrowings_promotes(self) -> None:
        assert schema.promote_header("BankBorrowings") == "BankBorrowings"

    def test_loans_from_related_companies_promotes(self) -> None:
        assert (schema.promote_header("LoansFromRelatedCompanies")
                == "LoansFromRelatedCompanies")

    def test_share_placing_proceeds_promotes(self) -> None:
        assert (schema.promote_header("SharePlacingProceeds")
                == "SharePlacingProceeds")

    def test_shares_issued_period_end_promotes(self) -> None:
        assert (schema.promote_header("SharesIssuedPeriodEnd")
                == "SharesIssuedPeriodEnd")


class TestMedliveDebtInvestmentsKpis:
    """Medlive Technology (2192.HK): the dcf-analyst's thesis is that the
    market is pricing the core operating business near zero -- DebtInvestments
    (bonds/wealth-management products funded by IPO proceeds) grew from
    CNY 0 (FY2023) to CNY 2,535.8m (H1-2026) and alone is worth more per
    share than the quoted price. OtherIncomeAndGains (mostly interest income
    off that same balance, CNY 193.5m FY2025) is a material chunk of
    reported profit. Both populated in `kpis` but absent from PROMOTE_KPIS,
    so the dashboard could not chart either."""

    def test_debt_investments_promotes(self) -> None:
        assert schema.promote_header("DebtInvestments") == "DebtInvestments"

    def test_other_income_and_gains_promotes(self) -> None:
        assert (schema.promote_header("OtherIncomeAndGains")
                == "OtherIncomeAndGains")


class TestBocHongKongLoanBookKpis:
    """BOC Hong Kong Holdings (2388.HK): AdvancesAndOtherAccounts (the loan
    book, HK$1.28tn FY2018 to HK$1.81tn H1 FY2026) is the standard bank
    growth KPI -- not derivable from TotalAssets, which also carries the
    much larger investment-securities book. LoanToDepositRatio (69.3%
    FY2022 down to 58.4% FY2025) is the funding/liquidity metric management
    discloses directly; deriving it from AdvancesAndOtherAccounts /
    CustomerDeposits would silently diverge from the disclosed ratio
    whenever the denominator basis differs. Both populated in `kpis` but
    absent from PROMOTE_KPIS, so the dashboard could not chart either."""

    def test_advances_and_other_accounts_promotes(self) -> None:
        assert (schema.promote_header("AdvancesAndOtherAccounts")
                == "AdvancesAndOtherAccounts")

    def test_loan_to_deposit_ratio_promotes(self) -> None:
        assert (schema.promote_header("LoanToDepositRatio")
                == "LoanToDepositRatio")


class TestShandongGoldPerpetualBondAndCasKpis:
    """Shandong Gold Mining (1787.HK): the CAS income statement folds
    perpetual-bond-holder profit and minority interest into reported
    NetIncome/ShareholdersEquity. PerpetualBonds (balance, CNY ~14bn FY2025)
    is deducted in the DCF equity bridge because it is economically senior
    to ordinary equity; PerpetualBondProfit and MinorityInterestProfit are
    the two leakage lines that separate reported NetIncome from
    NetIncomeOrdinary (the ex-perpetual-holder figure the DCF narrative
    quotes). OperatingCashFlowCAS exists because the company switched from
    IFRS to PRC GAAP (CAS) at FY2025 and OCF is not comparable like-for-like
    across the switch (FY2024 OCF: RMB20.3bn IFRS vs RMB13.3bn CAS)."""

    def test_perpetual_bonds_promotes(self) -> None:
        assert schema.promote_header("PerpetualBonds") == "PerpetualBonds"

    def test_perpetual_bond_profit_promotes(self) -> None:
        assert (schema.promote_header("PerpetualBondProfit")
                == "PerpetualBondProfit")

    def test_minority_interest_profit_promotes(self) -> None:
        assert (schema.promote_header("MinorityInterestProfit")
                == "MinorityInterestProfit")

    def test_net_income_ordinary_promotes(self) -> None:
        assert (schema.promote_header("NetIncomeOrdinary")
                == "NetIncomeOrdinary")

    def test_operating_cash_flow_cas_promotes(self) -> None:
        assert (schema.promote_header("OperatingCashFlowCAS")
                == "OperatingCashFlowCAS")

    def test_snake_case_variants_promote(self) -> None:
        assert schema.promote_header("bank_borrowings") == "BankBorrowings"
        assert (schema.promote_header("loans_from_related_companies")
                == "LoansFromRelatedCompanies")
        assert (schema.promote_header("share_placing_proceeds")
                == "SharePlacingProceeds")
        assert (schema.promote_header("shares_issued_period_end")
                == "SharesIssuedPeriodEnd")


class TestPradaLeaseKpis:
    """Prada S.p.A. (1913.HK): LeaseRepayments and InterestExpenseLeases are
    the IFRS-16 lease principal and lease interest lines, extracted under
    different literal spellings than DPC Dash's LeasePrincipalRepaid/
    LeaseInterestPaid (same concept -- the full lease cash cost the DCF
    deducts from reported FCF to reach Owner FCF, ~9.6% of revenue here).
    Both were populated FY2018/19-FY2024 in `kpis` but, as distinct
    spellings, had no KPI_ALIASES entry, so they fell through to the generic
    unmapped bucket instead of reusing the existing promoted headers."""

    def test_lease_repayments_aliases_to_lease_principal_repaid(self) -> None:
        assert schema.normalize_kpi("LeaseRepayments") == "LeasePrincipalRepaid"
        assert (schema.promote_header("LeaseRepayments")
                == "LeasePrincipalRepaid")

    def test_interest_expense_leases_aliases_to_lease_interest_paid(self) -> None:
        assert (schema.normalize_kpi("InterestExpenseLeases")
                == "LeaseInterestPaid")
        assert (schema.promote_header("InterestExpenseLeases")
                == "LeaseInterestPaid")


class TestKeHoldingsSegmentRevenueKpis:
    """KE Holdings Inc. / Beike (2423.HK): existing-home, new-home, home
    renovation & furnishing, home rental and emerging & other services are
    the five reported revenue-segment lines -- the mix shift (new-home down,
    home rental up) is the core of the qualitative thesis and is not
    derivable by splitting the single Revenue column. Populated in `kpis`
    for FY2024/FY2025 only but absent from PROMOTE_KPIS, so the dashboard
    could not chart the segment mix."""

    def test_revenue_existing_home_promotes(self) -> None:
        assert schema.promote_header("RevenueExistingHome") == "RevenueExistingHome"

    def test_revenue_new_home_promotes(self) -> None:
        assert schema.promote_header("RevenueNewHome") == "RevenueNewHome"

    def test_revenue_home_renovation_promotes(self) -> None:
        assert (schema.promote_header("RevenueHomeRenovation")
                == "RevenueHomeRenovation")

    def test_revenue_home_rental_promotes(self) -> None:
        assert schema.promote_header("RevenueHomeRental") == "RevenueHomeRental"

    def test_revenue_emerging_other_promotes(self) -> None:
        assert (schema.promote_header("RevenueEmergingOther")
                == "RevenueEmergingOther")


class TestHealthyWayRedemptionLiabilityKpis:
    """HealthyWay Inc. (2587.HK): a pre-IPO redemption liability on
    convertible-redeemable preferred shares drove the FY2023/FY2024
    reported net losses (-RMB313.9m/-269.2m) almost entirely through
    non-cash fair-value remeasurement -- pre-redemption PBT was actually
    +RMB4m/+9m those years, and the liability was extinguished at the
    Dec-2024 IPO (nil from FY2025). RedemptionLiabilityChange isolates that
    non-operating charge against NetIncome/OperatingIncome.
    NetIncomeAttributable is net income after minority interests, which
    diverges from the total NetIncome column by several million per period.
    Both populated in `kpis` but absent from PROMOTE_KPIS, so the dashboard
    could not chart either."""

    def test_redemption_liability_change_promotes(self) -> None:
        assert (schema.promote_header("RedemptionLiabilityChange")
                == "RedemptionLiabilityChange")

    def test_net_income_attributable_promotes(self) -> None:
        assert (schema.promote_header("NetIncomeAttributable")
                == "NetIncomeAttributable")


class TestConsumerInternetKpiPromotion:
    """Users x monetisation is how an ad-supported or social business is
    read (RDDT: revenue +61% = DAUq +18% x ARPU +36%), and the user metric
    is the early warning that growth is about to stop. ARPU was absent from
    PROMOTE_KPIS (only MobileARPU/BlendedARPU, two telcos), so SPOT's stored
    ARPU never reached a chart, and no geographic split existed at all --
    for RDDT the US-vs-international gap is the whole runway argument.
    """

    @pytest.mark.parametrize("name", [
        "ARPU", "ARPUUS", "ARPUIntl", "DAUUS", "DAUIntl", "WAU",
        "AdRevenue", "ActiveAdvertisers", "ARPP",
    ])
    def test_promoted(self, name):
        assert schema.promote_header(name) == name

    @pytest.mark.parametrize(("spelling", "canon"), [
        ("DAUq", "DAU"),
        ("Daily Active Uniques", "DAU"),
        ("daily_active_users", "DAU"),
        ("Monthly Active Users", "MAU"),
        ("Average Revenue Per User", "ARPU"),
        ("average_revenue_per_unique", "ARPU"),
        ("Advertising Revenue", "AdRevenue"),
    ])
    def test_common_spellings_resolve_to_one_name(self, spelling, canon):
        assert schema.normalize_kpi(spelling) == canon
        assert schema.promote_header(spelling) == canon
