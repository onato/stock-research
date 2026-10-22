#!/usr/bin/env python3
"""The canonical metrics schema, shared by every ticker in this repo.

Cross-ticker screening only works if every ticker DB declares the SAME
core table. Today's 67 metrics CSVs have 67 distinct header shapes across
382 distinct column names -- the same metric spelled `EPS`, `EPSBasic`,
`EPSDiluted`, `EPS_Diluted` -- so even a hand-written query misses rows.

The column set below is not invented: it is what the existing CSVs already
converge on (`Period` in 100% of them, TotalDebt/ShareholdersEquity/
Revenue/NetIncome/FreeCashFlow in 76-93%), plus the handful the DCF needs.

Anything company-specific (SubscriptionRevenue, RnDExpense, ARR) belongs in
the `kpis` long-form table, NOT as a new core column -- adding columns per
ticker is exactly the drift this replaces.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

# Core columns, in the order they appear in the exported CSV.
# (column_name, sql_type, csv_header) -- csv_header preserves the existing
# CamelCase the dashboards already read.
CORE_COLUMNS: list[tuple[str, str, str]] = [
    ("period",               "TEXT",   "Period"),
    ("revenue",              "DOUBLE", "Revenue"),
    ("cost_of_revenue",      "DOUBLE", "CostOfRevenue"),
    ("gross_profit",         "DOUBLE", "GrossProfit"),
    ("gross_margin",         "DOUBLE", "GrossMargin"),
    ("operating_income",     "DOUBLE", "OperatingIncome"),
    ("operating_margin",     "DOUBLE", "OperatingMargin"),
    ("ebitda",               "DOUBLE", "EBITDA"),
    ("net_income",           "DOUBLE", "NetIncome"),
    ("net_margin",           "DOUBLE", "NetMargin"),
    ("eps",                  "DOUBLE", "EPS"),
    ("operating_cash_flow",  "DOUBLE", "OperatingCashFlow"),
    ("capex",                "DOUBLE", "CapEx"),
    ("free_cash_flow",       "DOUBLE", "FreeCashFlow"),
    ("shareholders_equity",  "DOUBLE", "ShareholdersEquity"),
    ("total_assets",         "DOUBLE", "TotalAssets"),
    ("total_liabilities",    "DOUBLE", "TotalLiabilities"),
    ("total_debt",           "DOUBLE", "TotalDebt"),
    ("cash_and_equivalents", "DOUBLE", "CashAndEquivalents"),
    ("shares_outstanding",   "DOUBLE", "SharesOutstanding"),
    ("stock_based_comp",     "DOUBLE", "StockBasedComp"),
    ("dividend_per_share",   "DOUBLE", "DividendPerShare"),
    ("ebitda_before_significant",              "DOUBLE", "EBITDABeforeSignificant"),
    ("revenue_continuing",                     "DOUBLE", "RevenueContinuing"),
    ("ebitda_continuing_before_significant",   "DOUBLE", "EBITDAContinuingBeforeSignificant"),
    ("ebit_continuing_before_significant",     "DOUBLE", "EBITContinuingBeforeSignificant"),
    ("units",                "TEXT",   "Units"),
    ("currency",             "TEXT",   "Currency"),
]

CORE_NAMES: list[str] = [c[0] for c in CORE_COLUMNS]

# Columns whose stored value is in the row's `units` scale (money and the share
# count, which the filings print on the same scale). Per-share and percentage
# columns are scale-free. The metrics_normalized view, the warehouse and the
# dashboard all scale exactly this set to millions.
MONEY_COLUMNS: list[str] = [
    "revenue", "cost_of_revenue", "gross_profit", "operating_income", "ebitda", "net_income",
    "operating_cash_flow", "capex", "free_cash_flow", "shareholders_equity", "total_assets",
    "total_liabilities", "total_debt", "cash_and_equivalents", "stock_based_comp",
    "ebitda_before_significant", "revenue_continuing", "ebitda_continuing_before_significant",
    "ebit_continuing_before_significant",
]
UNIT_FACTORS: dict[str, float] = {
    "absolute": 1e-6, "absolute dollars": 1e-6, "units": 1e-6, "dollars": 1e-6,
    "thousands": 1e-3, "millions": 1.0, "billions": 1e3,
}
CSV_HEADERS: list[str] = [c[2] for c in CORE_COLUMNS]

# Parsed from `period` by periods.py and stored beside it, so SQL can order
# chronologically and pick 12-month rows without re-parsing labels
# (`ORDER BY period` is lexical: `9M 2021` < `FY2021` < `Q1 2021`). Derived,
# not core: they never reach the CSV, and backfill_period_columns() can
# always regenerate them.
DERIVED_COLUMNS: list[tuple[str, str]] = [
    ("period_type", "TEXT"),
    ("fiscal_year", "INTEGER"),
    ("months",      "INTEGER"),
]

# Existing CSV headers -> core column. Drawn from the aliases actually
# observed across the 67 committed CSVs, plus the legacy names called out
# in financial-parser.md (SBC / StockBasedComp / ShareBasedComp).
ALIASES: dict[str, str] = {
    "period": "period",
    "revenue": "revenue", "netsales": "revenue", "totalrevenue": "revenue",
    "operatingrevenue": "revenue", "sales": "revenue",
    "grossprofit": "gross_profit",
    "grossmargin": "gross_margin", "grossmarginpct": "gross_margin",
    "operatingincome": "operating_income", "ebit": "operating_income",
    "operatingprofit": "operating_income",
    "operatingmargin": "operating_margin",
    "ebitda": "ebitda", "adjustedebitda": "ebitda",
    "netincome": "net_income", "netprofit": "net_income",
    "netincomeloss": "net_income", "profitaftertax": "net_income",
    "netmargin": "net_margin", "netprofitmargin": "net_margin",
    "eps": "eps", "epsbasic": "eps", "eps_basic": "eps",
    "epsdiluted": "eps", "eps_diluted": "eps", "dilutedeps": "eps",
    "operatingcashflow": "operating_cash_flow", "ocf": "operating_cash_flow",
    "cashfromoperations": "operating_cash_flow",
    "capex": "capex", "capitalexpenditure": "capex",
    "capitalexpenditures": "capex",
    "freecashflow": "free_cash_flow", "fcf": "free_cash_flow",
    "shareholdersequity": "shareholders_equity", "equity": "shareholders_equity",
    "totalequity": "shareholders_equity", "bookvalue": "shareholders_equity",
    "totalassets": "total_assets",
    "totalliabilities": "total_liabilities",
    "costofrevenue": "cost_of_revenue", "costofsales": "cost_of_revenue",
    "costofgoodssold": "cost_of_revenue", "cogs": "cost_of_revenue",
    "dividendpershare": "dividend_per_share", "dps": "dividend_per_share",
    "totaldebt": "total_debt", "debt": "total_debt",
    "cashandequivalents": "cash_and_equivalents", "cash": "cash_and_equivalents",
    "cashandcashequivalents": "cash_and_equivalents",
    "sharesoutstanding": "shares_outstanding",
    "dilutedshares": "shares_outstanding",
    "sharesoutstandingdiluted": "shares_outstanding",
    "stockbasedcomp": "stock_based_comp", "sbc": "stock_based_comp",
    "sharebasedcomp": "stock_based_comp",
    "stockbasedcompensation": "stock_based_comp",
    "ebitdabeforesignificant": "ebitda_before_significant",
    "ebitdabeforesignificantitems": "ebitda_before_significant",
    "revenuecontinuing": "revenue_continuing",
    "continuingrevenue": "revenue_continuing",
    "ebitdacontinuingbeforesignificant": "ebitda_continuing_before_significant",
    "ebitdabeforesignificantcontinuing": "ebitda_continuing_before_significant",
    "ebitcontinuingbeforesignificant": "ebit_continuing_before_significant",
    "ebitbeforesignificantcontinuing": "ebit_continuing_before_significant",
    "units": "units", "currency": "currency",
}


def normalize(header: object) -> str | None:
    """Map a CSV header to its core column, or None if it is a KPI.

    Case- and punctuation-insensitive: `Gross Margin`, `gross_margin` and
    `GrossMargin` all resolve to `gross_margin`.
    """
    key = "".join(ch for ch in str(header).lower() if ch.isalnum())
    return ALIASES.get(key)



# ---------------------------------------------------------------------------
# Promotable business KPIs
# ---------------------------------------------------------------------------
# `kpis` is long-form and uncanonicalised, and it is dominated by DCF plumbing
# rather than business metrics: across the 171 ticker DBs, 871 distinct names
# appear, 717 of them on exactly one ticker, and ~35% of all rows are
# owner-FCF components (InterestIncome on 67 tickers, ShareRepurchases 60,
# CashTaxesPaid 58). Those reach the valuation through dcf_context.py and
# must never become CSV columns.
#
# So promotion is an explicit whitelist, not a blocklist: a name earns a
# column by being listed here. That keeps the cross-ticker CSV shape
# predictable and keeps this module's whole purpose -- one schema, not 382
# columns -- intact.
#
# The vocabulary is flat and global rather than keyed by business type,
# because a business type is a property of the *ticker*, not of the export.
# export_csv.py has no business-type input and should stay a pure function of
# the DB; choosing which of the available columns to chart belongs one layer
# up, in the DashboardSpec.

# Canonical KPI name -> CSV header. Values must not collide with CSV_HEADERS.
PROMOTE_KPIS: dict[str, str] = {
    # Customers and usage
    "ActiveCustomers": "ActiveCustomers",
    "Customers": "Customers",
    "Subscribers": "Subscribers",
    "MAU": "MAU",
    "DAU": "DAU",
    # Ad-supported / social: users x monetisation, and the US-vs-rest split
    # that is the runway argument (RDDT: US ARPU $11.85 vs intl $2.26 a
    # quarter). ARPU was missing entirely -- SPOT's stored ARPU never charted.
    "WAU": "WAU",
    "DAUUS": "DAUUS",
    "DAUIntl": "DAUIntl",
    "ARPU": "ARPU",
    "ARPUUS": "ARPUUS",
    "ARPUIntl": "ARPUIntl",
    "ARPP": "ARPP",
    "AdRevenue": "AdRevenue",
    "ActiveAdvertisers": "ActiveAdvertisers",
    "BroadbandConnections": "BroadbandConnections",
    # XD Inc. (2400.HK): TapTap's PRC/international MAU split and the
    # games-vs-platform revenue mix are the two halves of the investment
    # thesis -- only disclosed in half-year filings, so these are sparse
    # (H1 FY2024-FY2026) by nature, not a parsing gap.
    "MPU": "MPU",
    "TapTapPRCMAU": "TapTapPRCMAU",
    "TapTapIntlMAU": "TapTapIntlMAU",
    "SegmentRevenueGames": "SegmentRevenueGames",
    "SegmentRevenueTapTap": "SegmentRevenueTapTap",
    # Volume
    "GMV": "GMV",
    "TPV": "TPV",
    "TotalOrders": "TotalOrders",
    "ProcessedVolume": "ProcessedVolume",
    "GrossBookings": "GrossBookings",
    # Beer sales volume in kilolitres (Tsingtao Brewery, 0168.HK): volume vs
    # price/mix is the premiumization thesis -- not derivable from Revenue.
    "SalesVolumeKL": "SalesVolumeKL",
    # CNOOC Limited (0883.HK): disclosed realized oil/gas prices and sales
    # volume are the standard E&P investor metrics (realized price vs.
    # benchmark, volume growth) -- not derivable from Revenue, which also
    # includes trading.
    "RealizedOilPrice": "RealizedOilPrice",
    "RealizedGasPrice": "RealizedGasPrice",
    "SalesVolume": "SalesVolume",
    # KE Holdings Inc. / Beike (2423.HK): the five reported revenue-segment
    # lines -- existing-home sales, new-home sales, home renovation &
    # furnishing, home rental, and emerging & other services. The mix shift
    # (new-home down, home rental sharply up) is the qualitative thesis and
    # is not derivable by splitting the single Revenue column. Populated in
    # `kpis` for FY2024/FY2025 only.
    "RevenueExistingHome": "RevenueExistingHome",
    "RevenueNewHome": "RevenueNewHome",
    "RevenueHomeRenovation": "RevenueHomeRenovation",
    "RevenueHomeRental": "RevenueHomeRental",
    "RevenueEmergingOther": "RevenueEmergingOther",
    # Pacific Basin Shipping (2343.HK): Time-Charter Equivalent earnings is
    # the standard dry-bulk shipping KPI (net voyage revenue after voyage
    # costs, benchmarked per vessel-day against the BHSI/BSI indices) -- not
    # derivable from Revenue, which also includes the separate chartered-in
    # 'Operating Activity' segment.
    "TCEEarnings": "TCEEarnings",
    # Chow Tai Fook Jewellery (1929.HK): fair-value loss/gain on gold loans,
    # reclassified out of COGS into "other gains and losses" from H1 FY2025.
    # It is the dominant driver of the net-income-vs-operating-cash-flow gap
    # (FY2026 profit +52.2% to HK$9,004m vs OCF of only HK$1,317m) and is not
    # derivable from any CSV column.
    "GoldLoanFairValueLoss": "GoldLoanFairValueLoss",
    # China Taiping Insurance Holdings (0966.HK): GrossPremiumsWritten is
    # the core insurer top-line KPI under the old IFRS 4 regime (populated
    # FY2017-FY2021 only, before the IFRS 17 switch replaced it with the
    # insurance service result presentation). NetInvestmentResult is the
    # dcf-analyst's flagged single biggest swing driver of reported
    # earnings (-HK$7.5bn FY2022 to +HK$14.6bn FY2025 per build notes).
    # RepoLiabilities (HK$106.7bn FY2025) is a material repo-funding line,
    # distinct from TotalDebt, that the DCF analysis flagged separately.
    "GrossPremiumsWritten": "GrossPremiumsWritten",
    "NetInvestmentResult": "NetInvestmentResult",
    "RepoLiabilities": "RepoLiabilities",
    # New China Life Insurance (1336.HK): GrossWrittenPremiums is the core
    # life-insurer top-line KPI, populated FY2016-FY2022 only -- it stops
    # appearing in the summary disclosure after the IFRS 17 transition, the
    # same pre/post-IFRS17 break as 0966.HK's GrossPremiumsWritten above, but
    # spelled differently in this filer's own tables.
    "GrossWrittenPremiums": "GrossWrittenPremiums",
    # China Pacific Insurance Group (2601.HK): InsuranceRevenue is the
    # IFRS 17-era top-line KPI (populated FY2022 onward, plus interim rows),
    # reported alongside the pre-IFRS17 GrossWrittenPremiums above -- the
    # two never coexist for the same period, but each is the real top line
    # for its own era and neither is derivable from the other.
    "InsuranceRevenue": "InsuranceRevenue",
    # China Railway Construction Corporation (1186.HK): total debt rose
    # from RMB333bn (FY2023) to RMB728bn (H1 FY2026). OtherEquityInstruments
    # (perpetual bonds, RMB59.5bn FY2023 to RMB72.3bn H1 FY2026) are a
    # debt-like claim classified in equity -- sitting outside TotalDebt and
    # not derivable from the existing balance-sheet columns.
    "OtherEquityInstruments": "OtherEquityInstruments",
    # Shanghai Conant Optical (2276.HK): short-term wealth-management
    # products held at fair value through P&L (RMB788.5m FY2025, RMB1,024.2m
    # H1 FY2026) are a material, fast-growing balance-sheet item the
    # dcf-analyst's net-cash bridge explicitly adds back against borrowings
    # (net cash RMB1,717.7m vs. reported cash-only RMB1,438.3m at H1 FY2026)
    # -- not derivable from CashAndEquivalents alone.
    "ShortTermInvestmentsFVTPL": "ShortTermInvestmentsFVTPL",
    # Core telecom-services revenue (China Mobile Limited, 0941.HK): total
    # Revenue also includes product/terminal sales and the fast-growing
    # computing/AIDC/AI services segments, so the mix-shift thesis -- decade
    # of core mobile/broadband/IoT growth decelerating, now negative YoY in
    # H1 FY2026 -- needs this line split out from the blended Revenue column.
    "TelecomServicesRevenue": "TelecomServicesRevenue",
    # Money transfer (Wise, WISE.L): the volume behind the take rate, and the
    # customer money that earns ~a third of net revenue as interest.
    "CrossBorderVolume": "CrossBorderVolume",
    "CustomerHoldings": "CustomerHoldings",
    "CustomerBalances": "CustomerBalances",
    "AssetsUnderCustody": "AssetsUnderCustody",
    # Unit economics
    "AOV": "AOV",
    "CAC": "CAC",
    "MarketingROI": "MarketingROI",
    "TakeRate": "TakeRate",
    "RevenuePerActiveCustomer": "RevenuePerActiveCustomer",
    "RepeatOrdersPctTotal": "RepeatOrdersPctTotal",
    # Marketing cost lines
    "MarketingExpense": "MarketingExpense",
    "MarketingPctRevenue": "MarketingPctRevenue",
    # R&D expense (Lepu ScienTech Medical Technology, 2291.HK): a medical
    # device maker's spend on its biodegradable-occluder and TAVR pipeline
    # -- the qualitative thesis's main forward driver -- sits inside
    # OperatingIncome's deduction and is not broken out on any existing
    # column.
    "RnDExpense": "RnDExpense",
    # IT-services unit economics (CGI Inc., GIB-A.TO): Bookings is new
    # contract signings in the period; BookToBill (Bookings/Revenue, %)
    # signals forward revenue visibility -- above 100% means the backlog is
    # growing faster than revenue is being recognized. DaysSalesOutstanding
    # is the collections/working-capital health check. All three are
    # disclosed every quarter with no core_metrics column.
    "Bookings": "Bookings",
    "BookToBill": "BookToBill",
    "DaysSalesOutstanding": "DaysSalesOutstanding",
    # Oil & gas non-IFRS profitability measure (earnings before interest,
    # tax, depreciation, amortisation AND exploration expense) -- distinct
    # from the generic EBITDA column (Santos, STO.AX)
    "EBITDAX": "EBITDAX",
    # Recurring revenue. A company that REDEFINES its ARR needs the old and
    # new series in separate columns -- Adobe reported Digital Media ARR
    # ($19.20bn) through FY2025 and Total Adobe ARR ($26.06bn) from FY2026,
    # and one column splices them into a 36% jump over reported 10.9%
    # growth. The FY2025 10-K discloses both, which is the bridge.
    "ARR": "ARR",
    "DigitalMediaARR": "DigitalMediaARR",
    "TotalAdobeARR": "TotalAdobeARR",
    "SubscriptionRevenue": "SubscriptionRevenue",
    "NetRevenueRetention": "NetRevenueRetention",
    "ChurnRate": "ChurnRate",
    "CustomerRetentionRate": "CustomerRetentionRate",
    # Segment-level average revenue per customer (TradeWindow, TWL.NZ):
    # shippers and freight forwarders are priced and adopt modules
    # differently, so a single blended ARPC would hide which segment is
    # actually driving the per-customer expansion story.
    "ARPCShippers": "ARPCShippers",
    "ARPCFreightForwarders": "ARPCFreightForwarders",
    # Cash raised from new share issuance in the period -- a financing
    # figure, not an operating KPI, but load-bearing for a company whose
    # dilution history is a named risk (TWL.NZ has raised via placement/SPP
    # in nearly every year since IPO).
    "ShareIssuanceProceeds": "ShareIssuanceProceeds",
    # BNPL lender's receivables book and funding-facility cash (Zip Co,
    # ZIP.AX): CustomerReceivables is the loan book size -- the core driver
    # of the business, analogous to a bank's loan portfolio -- with history
    # back to FY2020. RestrictedCash is cash pledged to warehouse funding
    # facilities, distinct from cash genuinely available to the business; a
    # blended CashAndEquivalents figure hides that split.
    "CustomerReceivables": "CustomerReceivables",
    "RestrictedCash": "RestrictedCash",
    # Pledged/restricted bank deposits securing borrowings (Fufeng Group,
    # 0546.HK): management discloses these separately from free cash, and
    # they are material to the leverage story -- RMB7.7bn pledged against
    # RMB21.3bn borrowings alongside RMB13.9bn of unrestricted cash.
    "RestrictedBankBalances": "RestrictedBankBalances",
    # Segment revenue (WasteCo Group, WCO.NZ): three operating lines --
    # waste collection, sweeping services, industrial cleaning -- reverse-
    # listed into a shell in FY2023, so a single blended Revenue column
    # hides which segment is driving growth (sweeping services roughly
    # doubled FY2025->FY2026 while waste collection grew ~10%).
    "WasteCollectionRevenue": "WasteCollectionRevenue",
    "SweepingServicesRevenue": "SweepingServicesRevenue",
    "IndustrialCleaningRevenue": "IndustrialCleaningRevenue",
    # Testing/inspection two-segment split (ALS Limited, ALQ.AX): the group
    # restructured from three reporting segments to two -- Commodities and
    # Life Sciences -- so the split is only available for FY2025/FY2026 and
    # their half-years. Underlying* are ALS's own non-IFRS measures that
    # management and analysts anchor on, distinct from statutory NPAT/EBIT/
    # EBITDA/EPS.
    "SegmentRevenueCommodities": "SegmentRevenueCommodities",
    "SegmentRevenueLifeSciences": "SegmentRevenueLifeSciences",
    # Healthcare/Industrial two-segment split (Ansell Limited, ANN.AX): both
    # segments grow revenue and expand EBIT margin at different rates, so a
    # single blended Revenue/EBIT column hides which segment is driving the
    # investment case.
    "HealthcareRevenue": "HealthcareRevenue",
    "IndustrialRevenue": "IndustrialRevenue",
    "HealthcareEBIT": "HealthcareEBIT",
    "IndustrialEBIT": "IndustrialEBIT",
    "HealthcareEBITMargin": "HealthcareEBITMargin",
    "IndustrialEBITMargin": "IndustrialEBITMargin",
    "HealthcareOrganicGrowth": "HealthcareOrganicGrowth",
    "IndustrialOrganicGrowth": "IndustrialOrganicGrowth",
    "OrganicRevenueGrowth": "OrganicRevenueGrowth",
    # Three-segment revenue split (Qunabox Group, 0917.HK): marketing
    # services (AI-enabled interactive marketing terminal network, ~84% of
    # FY2025 revenue) vs merchandise sales (smart retail vending, ~12%) vs
    # other/IT services (~4%) -- a single blended Revenue column hides which
    # segment is driving the mix, and marketing services is growing faster
    # than the other two.
    "MarketingServicesRevenue": "MarketingServicesRevenue",
    "MerchandiseSalesRevenue": "MerchandiseSalesRevenue",
    "OtherServicesRevenue": "OtherServicesRevenue",
    "AdjustedEBITMargin": "AdjustedEBITMargin",
    "AdjustedEPS": "AdjustedEPS",
    # FirstService Corporation (FSV.TO): Residential (property management)
    # vs Brands (the franchise portfolio: California Closets, Paul Davis,
    # CertaPro, etc) two-segment split. The segments have different growth
    # and margin profiles -- Brands grew Adjusted EBITDA to a wider margin
    # than Residential through FY2025 -- so a single blended Revenue/
    # AdjustedEBITDA column hides which segment drives the mix shift. Both
    # had history back to FY2015/Q1-2016 stranded in kpis.
    "SegmentRevenueResidential": "SegmentRevenueResidential",
    "SegmentRevenueBrands": "SegmentRevenueBrands",
    "SegmentAdjustedEBITDAResidential": "SegmentAdjustedEBITDAResidential",
    "SegmentAdjustedEBITDABrands": "SegmentAdjustedEBITDABrands",
    # Customer concentration (Appen Limited, APX.AX): top-5-customer share of
    # revenue is a named high-severity business risk -- historically
    # dependent on a handful of big-tech accounts, and the abrupt loss of
    # Google's global services contract in FY2024 is the case study.
    "TopFiveCustomerConcentration": "TopFiveCustomerConcentration",
    "UnderlyingEBIT": "UnderlyingEBIT",
    "UnderlyingEBITDA": "UnderlyingEBITDA",
    "UnderlyingEPS": "UnderlyingEPS",
    "UnderlyingNPAT": "UnderlyingNPAT",
    # Order book / backlog (defense shipbuilder Austal Limited, ASB.AX):
    # forward-revenue visibility for a company that books multi-year
    # contracts -- material given competing takeover bids for its US
    # shipyard turn on that pipeline. WithOptions includes unexercised
    # contract options disclosed separately from the firm order book.
    "OrderBook": "OrderBook",
    "OrderBookWithOptions": "OrderBookWithOptions",
    # Seed capital vs core operating cash flow (multi-affiliate fund manager
    # Pinnacle Investment Management, PNI.AX): statutory operating cash flow
    # swings from -A$145m (FY2025) to +A$348m (FY2026) purely on seed-fund
    # subscriptions/redemptions timing, not the underlying management-fee
    # business. Both series have 22 populated periods (FY2016-FY2026 plus
    # half-years) but were absent from PROMOTE_KPIS, so the dashboard could
    # only show the noisy statutory OCF column.
    "OperatingCashFlowExSeed": "OperatingCashFlowExSeed",
    "NetSeedInvestmentFlow": "NetSeedInvestmentFlow",
    # Contracted order backlog (WSP Global, WSP.TO): forward-revenue
    # visibility for a project-based engineering consultancy -- WSP's own
    # disclosure frames backlog in months of revenue coverage. 55 periods
    # populated in `kpis` back to FY2015 but absent from PROMOTE_KPIS, so
    # the dashboard could not chart the trend investors watch.
    "Backlog": "Backlog",
    # Licensing/milestone revenue vs royalty revenue (biopharma licensor
    # HUTCHMED, 0013.HK): licensing/milestone income is lumpy -- Takeda's
    # FRUZAQLA upfront drove a one-off FY2023 spike ($279m vs $15-71m in
    # other years) -- while royalties from ex-China marketing partners
    # (Takeda, AstraZeneca, Eli Lilly) compound steadily. A blended Revenue
    # column hides that mix shift. ResearchAndDevelopment is the other half
    # of the story: spend more than halved FY2022->FY2025 as the pipeline
    # matured past its most capital-intensive trials.
    "LicensingRevenue": "LicensingRevenue",
    "RoyaltyRevenue": "RoyaltyRevenue",
    "ResearchAndDevelopment": "ResearchAndDevelopment",
    # Medlive Technology (2192.HK): DebtInvestments (bonds/wealth-management
    # products funded by IPO proceeds) grew from CNY 0 (FY2023) to
    # CNY 2,535.8m (H1-2026) -- the dcf-analyst's thesis is that this balance
    # alone is worth more per share than the quoted price, i.e. the core
    # operating business is priced near zero. OtherIncomeAndGains is mostly
    # interest income off that same balance (CNY 193.5m FY2025) and a
    # material chunk of reported profit.
    "DebtInvestments": "DebtInvestments",
    "OtherIncomeAndGains": "OtherIncomeAndGains",
    # Capitalised development costs (Techtronic Industries, 0669.HK): R&D
    # spend recognised as an intangible asset rather than expensed -- $135.6m
    # in FY2018 growing to $300-400m/yr by FY2022-FY2025, folded by the
    # financial-parser agent into the CapEx figure used for FCF. 11 periods
    # populated in `kpis` (FY2018-FY2026 plus half-years) but absent from
    # PROMOTE_KPIS, so the dashboard could not chart it separately from cash
    # CapEx.
    "CapitalisedDevelopment": "CapitalisedDevelopment",
    # Partly-owned subsidiary with minority interests (Sany Heavy Equipment
    # International Holdings, 0631.HK): NetIncomeTotal is total profit
    # including minority interests, vs the owners'-only NetIncome column --
    # informative here because NCI is in a deficit position (it took a share
    # of FY2025 losses), so attributable profit exceeds total profit, the
    # reverse of the usual direction. EPSBasic is basic EPS against the
    # diluted EPS already charted. ShortTermInvestments is current FVTPL
    # bank wealth-management products (note 13) -- near-cash but outside
    # CashAndEquivalents, and netted off against cash in the DCF's own
    # net-debt bridge.
    "NetIncomeTotal": "NetIncomeTotal",
    "EPSBasic": "EPSBasic",
    "ShortTermInvestments": "ShortTermInvestments",
    # Advertising/distribution spend (Mao Geping Cosmetics, 1318.HK):
    # selling expense runs ~47% of revenue, and the DCF's own margin-
    # compression thesis turns on its ratio to revenue rising (45.2% ->
    # 47.4% H1 FY2025 -> H1 FY2026) as advertising intensity stays high
    # against premium-beauty peers -- not derivable from OperatingIncome or
    # EBITDA alone.
    "SellingExpense": "SellingExpense",
    # Like-for-like restatement bridge (Geely Automobile Holdings, 0175.HK):
    # the FY2025 annual report restated FY2021-FY2024 financials after
    # consolidating ZEEKR, LYNK & CO and other Geely Holding brands bought
    # in from the parent group (FY2024 revenue as-filed RMB240.2bn vs
    # restated RMB275.9bn). core_metrics keeps each filing's
    # as-originally-filed numbers; these are the restated like-for-like
    # series disclosed alongside them, without which a reader can't see
    # that growth around FY2024-2025 is partly inorganic.
    "RevenueRestated": "RevenueRestated",
    "NetIncomeRestated": "NetIncomeRestated",
    "OCFRestated": "OCFRestated",
    "EBITDARestated": "EBITDARestated",
    # Pre-IPO convertible redeemable preferred shares (Bloks Group, 0325.HK):
    # classified as a liability (not equity) under IFRS until the January
    # 2026 IPO converted them to ordinary equity -- why shareholders' equity
    # flipped from negative to positive, and why the mark-to-market fair-
    # value loss on this liability drove reported net income deeply negative
    # pre-IPO despite an operationally improving business. Both series stop
    # at FY2024/H1 FY2024 once the IPO converts the liability away.
    "ConvertiblePreferredSharesLiability": "ConvertiblePreferredSharesLiability",
    "PreferredSharesFairValueLoss": "PreferredSharesFairValueLoss",
    "EBITDACompanyReportedInclDeemedDisposalGain": "EBITDACompanyReportedInclDeemedDisposalGain",
    # Property advertising vs financial services revenue split (REA Group,
    # REA.AX): the core listings business, financial services (Mortgage
    # Choice) is a bolt-on segment whose revenue steps up sharply -- from
    # ~$100m to ~$325m in FY2022 on the Mortgage Choice acquisition -- so a
    # single Revenue column hides the margin-mix shift toward the lower-margin
    # broking business.
    "PropertyAdvertisingRevenue": "PropertyAdvertisingRevenue",
    "FinancialServicesRevenue": "FinancialServicesRevenue",
    # Passenger vs cargo revenue split (Cathay Pacific Airways, 0293.HK): the
    # two halves of an airline's top line move on different drivers (travel
    # demand vs air-freight rates) and recovered on very different timelines
    # post-2020, so a single Revenue column hides which engine is actually
    # driving the recovery.
    "PassengerRevenue": "PassengerRevenue",
    "CargoRevenue": "CargoRevenue",
    # Five-segment revenue split (Breville Group, BRG.AX): the FY report
    # discloses revenue by APAC, Americas, EMEA, Global Product and
    # Distribution rather than a single blended Revenue line, and the mix is
    # shifting -- Americas overtook the rest of ROW combined by FY2026 while
    # Distribution has been flat-to-declining -- so a single Revenue column
    # hides which region is actually driving group growth.
    "SegmentRevenueAPAC": "SegmentRevenueAPAC",
    "SegmentRevenueAmericas": "SegmentRevenueAmericas",
    "SegmentRevenueEMEA": "SegmentRevenueEMEA",
    "SegmentRevenueGlobalProduct": "SegmentRevenueGlobalProduct",
    "SegmentRevenueDistribution": "SegmentRevenueDistribution",
    # Four-segment revenue/EBIT split post-Construction-divestiture
    # (Fletcher Building, FBU.AX): Light Building Products, Heavy Building
    # Materials, Distribution and Residential & Development are the
    # continuing segments from FY2026, and profitability by segment is more
    # informative than revenue alone for a company that just exited its
    # money-losing Construction arm. SignificantItems is a recurring
    # one-off-charge line (largest -644m NZD in FY2025, Iplex Pro-fit pipe
    # defects and Australian construction losses) that has hit nearly every
    # year -- a genuine pattern, not noise to hide.
    "SegmentRevenueHeavyBuildingMaterials": "SegmentRevenueHeavyBuildingMaterials",
    "SegmentRevenueLightBuildingProducts": "SegmentRevenueLightBuildingProducts",
    "SegmentRevenueResidentialAndDevelopment": "SegmentRevenueResidentialAndDevelopment",
    "SegmentEBITDistribution": "SegmentEBITDistribution",
    "SegmentEBITHeavyBuildingMaterials": "SegmentEBITHeavyBuildingMaterials",
    "SegmentEBITLightBuildingProducts": "SegmentEBITLightBuildingProducts",
    "SegmentEBITResidentialAndDevelopment": "SegmentEBITResidentialAndDevelopment",
    "SignificantItems": "SignificantItems",
    # Consolidated-vs-attributable split for a majority-owned holding company
    # (PCCW Limited, 0008.HK, ~52% of HKT): PCCW consolidates 100% of HKT's
    # profit and equity, but most of it belongs to non-controlling interests
    # before reaching PCCW's own shareholders -- ProfitForPeriodTotal (group,
    # positive) and TotalEquityInclNCI (group, positive) are far larger than
    # the attributable NetIncome/ShareholdersEquity columns (both negative in
    # FY2025), which is the central minority-interest story for this ticker.
    "ProfitForPeriodTotal": "ProfitForPeriodTotal",
    "TotalEquityInclNCI": "TotalEquityInclNCI",
    "CapexPPE": "CapexPPE",
    "CapexIntangibles": "CapexIntangibles",
    # Same minority-interest story for ATCO Ltd. (ACO-X.TO, ~52.4% of CU.TO):
    # core_metrics.net_income is ATCO-owners' share only. NetIncomeInclNCI is
    # the consolidated figure before the CU minority's cut -- e.g. Q3 FY2025
    # 145 consolidated vs 85 attributable, a ~41% minority slice of that
    # quarter's income, materially above the ~47.6% CU minority stake read
    # off equity alone because the split of consolidated income swings
    # quarter to quarter.
    "NetIncomeInclNCI": "NetIncomeInclNCI",
    # Henderson Land Development (0012.HK): related-party debt and the JV/
    # associate income it does not consolidate. AmountDueToFellowSubsidiary
    # is an interest-bearing loan from the controlling family's own private
    # finance company (Henderson Finance Co) -- ~HK$77bn at H1 FY2026,
    # roughly the size of reported TotalDebt, so it is debt-like and
    # material, not a footnote curiosity. ShareOfAssociatesProfit and
    # ShareOfJointVenturesProfit are the group's share of profit from
    # unconsolidated stakes (including ~41.53% of HK & China Gas / Towngas)
    # with their own trend, distinct from consolidated NetIncome.
    # InvestmentPropertyFairValueChange is the IAS 40 revaluation swing the
    # financial-parser excluded from this company's operating/net income
    # definition.
    "AmountDueToFellowSubsidiary": "AmountDueToFellowSubsidiary",
    "ShareOfAssociatesProfit": "ShareOfAssociatesProfit",
    "ShareOfJointVenturesProfit": "ShareOfJointVenturesProfit",
    "InvestmentPropertyFairValueChange": "InvestmentPropertyFairValueChange",
    # Hang Lung Properties (0101.HK): same IAS 40 revaluation-noise pattern as
    # Henderson Land above, but this company's worksheet carried the swing
    # and the revaluation-inclusive operating line under its own disclosed
    # names rather than Henderson's -- kept distinct so neither aliases over
    # the other's sign convention or basis.
    "InvestmentPropertyRevaluation": "InvestmentPropertyRevaluation",
    "OperatingProfitAfterRevaluation": "OperatingProfitAfterRevaluation",
    # China Merchants Port Holdings (0144.HK): a port operator whose
    # consolidated subsidiaries are only half the earnings story -- roughly
    # 40-50% of pre-tax profit comes from equity-accounted associates/JVs
    # (SIPG Shanghai, Qingdao, Tianjin, Terminal Link, Djibouti's PDSA) that
    # never touch consolidated revenue. It fell 23.7% YoY in FY2025 (6,513m
    # -> 4,970m) and drove an 18.5% drop in attributable profit even as
    # consolidated revenue grew 12.8% -- the combined associates+JV line the
    # filing discloses as one figure, distinct from Henderson Land's split
    # ShareOfAssociatesProfit/ShareOfJointVenturesProfit above.
    "ShareOfAssocJVProfit": "ShareOfAssocJVProfit",
    # Property / financial sector staples
    "AFFO": "AFFO",
    "AISC": "AISC",                       # gold miners: all-in sustaining cost per oz
    "GoldProduction": "GoldProduction",
    "CopperProduction": "CopperProduction",
    "GoldPriceAchieved": "GoldPriceAchieved",
    "RealizedCopperPrice": "RealizedCopperPrice",  # copper byproduct USD/lb price
    "GoldSoldManagedOz": "GoldSoldManagedOz",  # gold oz sold by managed ops
    # Royalty/streaming companies (Franco-Nevada, FNV.TO): GEOsSold (gold
    # equivalent ounces sold) is the volume driver the way units shipped is
    # for a manufacturer -- it blends gold, silver, PGM and energy royalty
    # streams onto one comparable axis. AverageGoldPrice is the realized
    # price that drives the top line without a mine to run; CashCostsPerGEO
    # is the margin metric specific to the royalty model (a cost per ounce
    # with no mining opex behind it).
    "GEOsSold": "GEOsSold",
    "AverageGoldPrice": "AverageGoldPrice",
    "CashCostsPerGEO": "CashCostsPerGEO",
    # Wheaton Precious Metals (WPM.TO): same streaming/royalty model as
    # Franco-Nevada above but a distinct disclosed vocabulary -- gold and
    # silver broken out separately (volume, realized price, cash cost per
    # ounce) as well as the blended GEO figures, plus the per-GEO cash
    # operating margin (realized price less cash cost) that is the model's
    # entire economic engine.
    "GEOProduced": "GEOProduced",
    "GEOSold": "GEOSold",
    "GoldOuncesProduced": "GoldOuncesProduced",
    "GoldOuncesSold": "GoldOuncesSold",
    "SilverOuncesProduced": "SilverOuncesProduced",
    "SilverOuncesSold": "SilverOuncesSold",
    "RealizedGoldPrice": "RealizedGoldPrice",
    # Zijin Gold International (2259.HK): proved+probable reserves and
    # measured+indicated+inferred resources, in tonnes of contained gold --
    # the resource-base size the investment thesis is built on (mine life,
    # future production capacity), disclosed once a year and not derivable
    # from any income-statement or cash-flow column.
    "GoldReservesTonnes": "GoldReservesTonnes",
    "GoldResourcesTonnes": "GoldResourcesTonnes",
    "RealizedSilverPrice": "RealizedSilverPrice",
    "RealizedPricePerGEO": "RealizedPricePerGEO",
    "CashCostPerGEO": "CashCostPerGEO",
    "GoldCashCostPerOz": "GoldCashCostPerOz",
    "SilverCashCostPerOz": "SilverCashCostPerOz",
    "CashOperatingMarginPerGEO": "CashOperatingMarginPerGEO",
    "GoldProduction_Cowal": "GoldProduction_Cowal",
    "GoldProduction_ErnestHenry": "GoldProduction_ErnestHenry",
    "GoldProduction_Mungari": "GoldProduction_Mungari",
    "GoldProduction_Northparkes": "GoldProduction_Northparkes",
    "GoldProduction_RedLake": "GoldProduction_RedLake",
    "AFFOPerShare": "AFFOPerShare",
    # Continuing vs discontinued operations (Tabcorp Holdings, TAH.AX):
    # the May-2022 Lottery Corporation demerger put a one-off A$6,894.3m
    # gain-on-demerger through FY2022 statutory NetIncome, which reads as
    # organic earnings unless continuing-ops profit is charted alongside it
    # -- continuing operations actually lost A$118.4m that year.
    "NetIncomeContinuing": "NetIncomeContinuing",
    "DiscontinuedOpsProfit": "DiscontinuedOpsProfit",
    # Fortive (FTV): the Jun-2025 Ralliant spin-off means core_metrics
    # net_income/operating_cash_flow are whole-company (incl. divested
    # Ralliant) through FY2025, while these kpis rows are the go-forward
    # continuing-operations-only view -- charting only the whole-company
    # column overstates FY2024/2025 margins by as much as ~50% on OCF.
    "NetIncomeDiscontinued": "NetIncomeDiscontinued",
    "OperatingCashFlowContinuing": "OperatingCashFlowContinuing",
    "NAVPerShare": "NAVPerShare",
    "NTAPerShare": "NTAPerShare",
    # Investment-trust specific disclosures (Scottish Mortgage Investment
    # Trust plc, SMT.L): InvestmentManagementFee is the fee drag on returns,
    # and RevenueReturn is the income-side half of UK trust total-return
    # reporting (distinct from capital return) used to size dividend cover
    # independent of portfolio revaluations. Neither has a core_metrics
    # column.
    "InvestmentManagementFee": "InvestmentManagementFee",
    "RevenueReturn": "RevenueReturn",
    # SSE plc (SSE.L): statutory operating income/net income are dominated
    # by derivative re-measurement swings unrelated to the underlying
    # business (FY2023 statutory operating income was -GBP146.3m on a year
    # SSE itself called a record), so the financial-parser and dcf-analyst
    # anchor on SSE's own adjusted "before exceptional items" lines instead.
    # SharesInIssuePeriodEnd (vs the weighted-average core_metrics column)
    # and TotalEquityInclHybridNCI (vs the ordinary-shareholders-only
    # equity column) matter because the ~GBP2bn Nov-2025 placing and SSE's
    # hybrid capital move the period-end basis well away from the
    # weighted-average/ordinary-only ones used elsewhere on the dashboard.
    "OperatingProfitBeforeExceptionals": "OperatingProfitBeforeExceptionals",
    "ProfitBeforeExceptionalsAttributable": "ProfitBeforeExceptionalsAttributable",
    "SharesInIssuePeriodEnd": "SharesInIssuePeriodEnd",
    "TotalEquityInclHybridNCI": "TotalEquityInclHybridNCI",
    # Ambev's own headline non-GAAP profitability measure (Ambev S.A.,
    # ABEV): disclosed every quarter in its press releases, distinct from
    # the audited EBITDA core_metrics column the financial-parser built
    # from income-from-operations + D&A rather than Ambev's own figure.
    "NormalizedEBITDA": "NormalizedEBITDA",
    # Funds From Operations (REIT/stapled-security non-IFRS cash measure,
    # Stockland Group SGP.AX): FFO strips non-cash property revaluations and
    # straight-lining out of statutory NPAT and is the number a REIT's own
    # distribution policy is set against, distinct from AFFO (FFO less
    # maintenance capex) already promoted above.
    "FFO": "FFO",
    "FFOPerShare": "FFOPerShare",
    "PreTaxFFO": "PreTaxFFO",
    "PreTaxFFOPerShare": "PreTaxFFOPerShare",
    "Gearing": "Gearing",
    "Occupancy": "Occupancy",
    # Hotel revenue per available room (Millennium & Copthorne Hotels New
    # Zealand Limited, MCK.NZ) -- the industry-standard yield metric,
    # distinct from Occupancy (a rate) and Revenue (not per-room).
    "RevPAR": "RevPAR",
    "WALT": "WALT",
    "AUM": "AUM",
    # A wealth manager's own named "Total AUM" figure (AMP.AX: Platforms,
    # Superannuation & Investments, NZ Wealth Management) -- kept as its own
    # column rather than aliased into the generic AUM above, mirroring the
    # qualified-ARR pattern: a company's headline AUM series must not
    # collapse with a segment or peer AUM figure reported alongside it.
    "TotalAUM": "TotalAUM",
    "NetInterestMargin": "NetInterestMargin",
    # Bank-quality core metrics (Westpac Banking Corporation, WBC.NZ): the
    # metrics analysts actually judge a bank on -- net interest margin,
    # capital adequacy, and return on (tangible) equity -- plus the income
    # mix and per-share book value the residual-income-on-tangible-book DCF
    # is anchored to. DividendPayoutRatio and SpecialDividendPerShare
    # matter because FY2024 paid a 15c special dividend on top of the
    # regular DPS series, which would otherwise look like a payout spike.
    "NIM": "NIM",
    "CET1Ratio": "CET1Ratio",
    "ROE": "ROE",
    "ROTE": "ROTE",
    "BookValuePerShare": "BookValuePerShare",
    "NetInterestIncome": "NetInterestIncome",
    "NonInterestIncome": "NonInterestIncome",
    "DividendPayoutRatio": "DividendPayoutRatio",
    "SpecialDividendPerShare": "SpecialDividendPerShare",
    # ANZ Group Holdings (ANZ.AX): the cash-earnings measure management
    # guides to and is judged on (distinct from statutory NetIncome), the
    # credit-cycle swing line (impairment charge/release), the balance-sheet
    # growth pair (deposit funding vs the loan book it funds), and the cost
    # line the cost-to-income ratio runs off.
    "CashProfit": "CashProfit",
    "CreditImpairmentCharge": "CreditImpairmentCharge",
    "CustomerDeposits": "CustomerDeposits",
    "NetLoansAndAdvances": "NetLoansAndAdvances",
    "OperatingExpenses": "OperatingExpenses",
    # Bendigo and Adelaide Bank (BEN.AX): the bank's own preferred profit
    # measure (cash earnings, distinct from statutory NetIncome), the
    # efficiency ratio it runs its cost base against, the credit-cycle swing
    # line, the balance-sheet growth pair (deposit funding vs the loan book
    # it funds), the statutory profitability ratio, and the two APRA
    # regulatory-capital/liquidity ratios alongside CET1Ratio.
    "CashEarnings": "CashEarnings",
    "CostToIncomeRatio": "CostToIncomeRatio",
    "CreditExpenses": "CreditExpenses",
    "Deposits": "Deposits",
    "NetLoans": "NetLoans",
    "ReturnOnEquity": "ReturnOnEquity",
    "RiskWeightedAssets": "RiskWeightedAssets",
    # Banco Bradesco (BBD): the total capital (Basel) ratio alongside
    # CET1Ratio, Bradesco's own broader loan-book measure, the insurance
    # segment's recurring profit contribution, and the 90-day non-performing
    # loan ratio -- the credit-quality indicator analysts watch a bank on.
    "BaselRatio": "BaselRatio",
    "ExpandedLoanPortfolio": "ExpandedLoanPortfolio",
    "InsuranceRecurringNetIncome": "InsuranceRecurringNetIncome",
    "NPLRatio90d": "NPLRatio90d",
    "LiquidityCoverageRatio": "LiquidityCoverageRatio",
    "NetStableFundingRatio": "NetStableFundingRatio",
    # Bank of Queensland (BOQ.AX): the gross loan book and total deposit
    # base -- the funding/lending pair the balance sheet runs on -- plus
    # cash EPS, the per-share measure management and the DCF lean on given
    # the FY2025 statutory/cash NPAT divergence (goodwill impairment).
    "GrossLoansAndAdvances": "GrossLoansAndAdvances",
    "TotalDeposits": "TotalDeposits",
    "CashEPS": "CashEPS",
    # An operating company's embedded consumer/commercial loan book (Turners
    # Automotive Group's Oxford Finance) -- the size driving its Finance
    # segment, distinct from a pure lender's balance sheet.
    "FinanceReceivables": "FinanceReceivables",
    # General insurer health metrics (Tower, TWR.NZ). Combined operating
    # ratio (claims + management expense ratio, both as % of net earned
    # premium) is the standard solvency/profitability read for a general
    # insurer; BAU claims ratio strips large/one-off events out of the
    # combined ratio so a spike from a single event (H1 FY2026's $18.5m of
    # large event claims vs $3.0m H1 FY2025) doesn't read as underlying
    # deterioration. Underlying profit after tax is the company's own
    # headline measure, distinct from statutory NPAT.
    "GrossWrittenPremium": "GrossWrittenPremium",
    "NetEarnedPremium": "NetEarnedPremium",
    "CombinedOperatingRatio": "CombinedOperatingRatio",
    "BAUClaimsRatio": "BAUClaimsRatio",
    "ManagementExpenseRatio": "ManagementExpenseRatio",
    "InsuranceServiceResult": "InsuranceServiceResult",
    "NetInsuranceFinancialResult": "NetInsuranceFinancialResult",
    # P&C insurer core-earnings lines (Intact Financial, IFC.TO): underwriting
    # income is the profit on the insurance book itself (premiums less claims
    # and expenses), separate from the investment portfolio's return: net
    # investment income. NOIPS (Net Operating Income Per Share) is the
    # company's own non-GAAP per-share operating metric, reported quarterly
    # alongside EPS.
    "UnderwritingIncome": "UnderwritingIncome",
    "NetInvestmentIncome": "NetInvestmentIncome",
    "NOIPS": "NOIPS",
    # PICC Property and Casualty (2328.HK): LossRatio and ExpenseRatio are
    # the two components of CombinedOperatingRatio (already promoted above)
    # -- a given COR can come from very different loss/expense splits, so
    # the mix is its own underwriting-quality signal, not derivable from COR
    # alone. SolvencyRatio is the C-ROSS regulatory capital-adequacy ratio
    # (~237% at H1 FY2026 per the qualitative analysis), a key risk
    # indicator distinct from any balance-sheet column.
    "LossRatio": "LossRatio",
    "ExpenseRatio": "ExpenseRatio",
    "SolvencyRatio": "SolvencyRatio",
    # Pre-revenue JV lithium developer (Argosy Minerals, AGY.AX): Argosy
    # cannot fund the Rincon project alone and holds it through an
    # equal-board equity-accounted JV (Puna Mining S.A.), so the funding
    # relationship -- cumulative advances made and the JV's carrying value
    # -- is the balance-sheet story in place of revenue/margin, and the
    # impairment/reversal swings on that carrying value (not operations)
    # dominate reported net income.
    "AdvancesToPunaMining": "AdvancesToPunaMining",
    "JVInvestmentPunaMining": "JVInvestmentPunaMining",
    "ExplorationAndEvaluationAssets": "ExplorationAndEvaluationAssets",
    "ImpairmentExpense": "ImpairmentExpense",
    "ImpairmentReversal": "ImpairmentReversal",
    # Almond grower/processor (Select Harvests, SHV.AX): the IAS 41
    # biological-asset fair-value swing on almond trees/crop is a major,
    # non-cash driver of reported earnings volatility -- it flipped FY2023
    # to an operating loss (-$74.5m) and swung +$58.8m in FY2025. Lease
    # principal repaid is the IFRS-16 financing outflow behind the orchard
    # and processing-plant lease fleet, distinct from OCF/CapEx.
    "BiologicalFairValueAdjustment": "BiologicalFairValueAdjustment",
    "LeasePrincipalPaid": "LeasePrincipalPaid",
    # Gentailer operating drivers (AGL Energy, AGL.AX): electricity
    # generation volume (GWh) tracks the coal fleet's decline toward its
    # 2030-2035 closures, and customer services (retail energy/telco
    # accounts, millions) is the base the margin/churn story is built on.
    "GenerationVolume": "GenerationVolume",
    "CustomerServices": "CustomerServices",
    "UnderlyingProfitAfterTax": "UnderlyingProfitAfterTax",
    "LargeEventClaims": "LargeEventClaims",
    "DividendPerShareDeclared": "DividendPerShareDeclared",
    "SharesDiluted": "SharesDiluted",
    # Stapled REIT + external funds manager two-leg split (SPG.NZ): the
    # property owner's gross rental income versus the manager's fee
    # income earned on AUM it does not own. Consolidated Revenue blends
    # both legs and hides which one is actually growing.
    "GrossRentalIncome": "GrossRentalIncome",
    "ManagementFeeIncome": "ManagementFeeIncome",
    # Net-revenue reporters (payments, marketplaces) have no gross Revenue
    # line -- NetRevenue is the top line, so it must reach the CSV.
    "NetRevenue": "NetRevenue",
    "PayablesToMerchants": "PayablesToMerchants",
    # ORA/DMF retirement operators (SUM.NZ, RYM.NZ, OCA.NZ). Statutory NPAT is
    # dominated by IAS 40 revaluation, so the company's own UnderlyingProfit is
    # the earnings measure the model runs off, and the ORA sales and portfolio
    # counts are the volume series behind it.
    "UnderlyingProfit": "UnderlyingProfit",
    # Conglomerate segment recurring profit (Swire Pacific, 0019.HK): statutory
    # net income is dominated by investment-property revaluation noise, so
    # management and analysts track recurring/underlying profit by division
    # instead -- Property (Swire Properties), Beverages (Swire Coca-Cola), the
    # Cathay Pacific equity-accounted group, wholly-owned HAECO plus other
    # aviation-services interests, Trading & Industrial, and Head Office/
    # Healthcare (typically negative: corporate costs net of the healthcare
    # bolt-on). RecurringUnderlyingProfit is the sum of the six divisions.
    "RecurringProfitProperty": "RecurringProfitProperty",
    "RecurringProfitBeverages": "RecurringProfitBeverages",
    "RecurringProfitCathayGroup": "RecurringProfitCathayGroup",
    "RecurringProfitHAECOAndOthers": "RecurringProfitHAECOAndOthers",
    "RecurringProfitTradingIndustrial": "RecurringProfitTradingIndustrial",
    "RecurringProfitHeadOfficeHealthcare": "RecurringProfitHeadOfficeHealthcare",
    "RecurringUnderlyingProfit": "RecurringUnderlyingProfit",
    "NPAT": "NPAT",
    "NetAssets": "NetAssets",
    # Pallet/crate/container pooling group capital-efficiency measures
    # (Brambles Limited, BXB.AX): Return on Capital Invested (ROCI) is the
    # company's own headline KPI, measured against Average Capital Invested
    # (the pooling equipment plus working-capital base); the Irrecoverable
    # Pooling Equipment Provision (IPEP) is Brambles' distinctive
    # pallet/crate loss charge, disclosed separately from depreciation.
    "ReturnOnCapitalInvested": "ReturnOnCapitalInvested",
    "AverageCapitalInvested": "AverageCapitalInvested",
    "IPEPExpense": "IPEPExpense",
    "CareFeesAndVillageServices": "CareFeesAndVillageServices",
    "DeferredManagementFees": "DeferredManagementFees",
    "NewSalesORA": "NewSalesORA",
    "ResalesORA": "ResalesORA",
    "TotalSalesORA": "TotalSalesORA",
    "NewUnitsDelivered": "NewUnitsDelivered",
    "RetirementUnitsInPortfolio": "RetirementUnitsInPortfolio",
    "CareUnitsInPortfolio": "CareUnitsInPortfolio",
    "DevelopmentMargin": "DevelopmentMargin",
    # Disclosed operating detail dashboards chart directly
    "EBITDAMargin": "EBITDAMargin",
    "EPS_Basic": "EPS_Basic",
    # The NZX retirement CSVs spell it BasicEPS and carry it (in cents) as a
    # column beside EPS. Kept as its own header rather than aliased onto
    # EPS_Basic, which would orphan the periods already under BasicEPS.
    "BasicEPS": "BasicEPS",
    "FTE": "FTE",
    # IFRS 15 disaggregation of revenue. A filer that discloses no cost of
    # revenue (Serko) has no gross margin, so the stream split is the only
    # view of business mix -- and where one stream is a single counterparty
    # (SupplierCommissionsRevenue is Booking.com for Business, ~61% of
    # SKO.NZ's FY2026 revenue) it is the concentration risk itself.
    "TravelPlatformBookingRevenue": "TravelPlatformBookingRevenue",
    "ExpensePlatformRevenue": "ExpensePlatformRevenue",
    # Residential land developer operating drivers (Winton Land, WIN.NZ):
    # settled units is the core volume series behind Revenue, and the
    # contracted pre-sale book is the forward-revenue-visibility signal --
    # both collapsed alongside the NZ housing downturn (units 565->266 FY23-
    # FY25; pre-sales $239.8m Dec-25 -> $27.4m Jun-26).
    "SettledUnits": "SettledUnits",
    "PreSaleBook": "PreSaleBook",
    "AvgRevenuePerUnit": "AvgRevenuePerUnit",
    # Bottler-industry standard volume KPI (Coca-Cola Europacific Partners,
    # CCEP): billions of unit cases sold in the period, the metric management
    # itself leads with alongside revenue -- distinct from revenue growth
    # since it strips out pricing/mix.
    "UnitCaseVolume": "UnitCaseVolume",
    "SupplierCommissionsRevenue": "SupplierCommissionsRevenue",
    "ServicesRevenue": "ServicesRevenue",
    "OtherRevenue": "OtherRevenue",
    # Liquidity where cash at bank is not the whole story. SKO.NZ holds
    # 14.146 as CashAndEquivalents against 40.0 of short-term deposits at
    # FY2026; the core column alone understates real liquidity ~4x.
    "TotalCashAndDeposits": "TotalCashAndDeposits",
    "ShortTermDeposits": "ShortTermDeposits",
    # Total income (revenue plus other income) and the company's own headline
    # earnings measure, plus capitalised development -- the cash cost of the
    # product that never reaches the income statement.
    "TotalIncome": "TotalIncome",
    "EBITDAFI": "EBITDAFI",
    "NetTangibleAssetsPerShare": "NetTangibleAssetsPerShare",
    "CapitalisedDevelopmentCosts": "CapitalisedDevelopmentCosts",
    # Multi-banner grocer segment split and channel mix. A grocer's equity
    # story outside the consolidated P&L lives in the geographic segment
    # split (Ahold Delhaize US vs Europe) and the online-sales channel mix,
    # plus the company's own non-GAAP underlying operating margin (the
    # measure management actually guides on) and the like-for-like
    # comparable-sales growth disclosed instead of same-store units.
    "USSegmentRevenue": "USSegmentRevenue",
    "EuropeSegmentRevenue": "EuropeSegmentRevenue",
    "USSegmentOperatingIncome": "USSegmentOperatingIncome",
    "EuropeSegmentOperatingIncome": "EuropeSegmentOperatingIncome",
    "OnlineSales": "OnlineSales",
    "NetConsumerOnlineSales": "NetConsumerOnlineSales",
    "UnderlyingOperatingMargin": "UnderlyingOperatingMargin",
    "ComparableSalesGrowthUS": "ComparableSalesGrowthUS",
    "ComparableSalesGrowthEurope": "ComparableSalesGrowthEurope",
    "StoreCount": "StoreCount",
    "EmployeesFTE": "EmployeesFTE",
    # Auntea Jenny (Shanghai) Industrial (2589.HK): franchise/wholesale
    # tea-shop operator where franchisee count alongside StoreCount is a
    # core growth KPI (5,455 FY2024 -> 6,974 FY2025 -> 8,014 H1 FY2026,
    # +39% store count YoY) -- not derivable from StoreCount, since
    # closures (~12%/yr) and onboarding move the two counts independently.
    "Franchisees": "Franchisees",
    # Port/terminal operators (SPN.NZ). Cargo tonnage is the operating
    # driver of a port -- the real KPI, not a proxy off revenue -- and
    # customer concentration (Tiwai smelter is ~24% of SPN.NZ FY2026
    # volume) is the single most important risk fact about the business.
    # NormalisedNPAT is the company's own earnings measure stripping
    # one-off tax items (e.g. the FY2024 deferred-tax charge) that
    # statutory NetIncome would otherwise misread as an operating dip.
    "CargoVolumeTonnes": "CargoVolumeTonnes",
    "TiwaiVolumeTonnes": "TiwaiVolumeTonnes",
    "BulkCargoVolumeTonnes": "BulkCargoVolumeTonnes",
    "ContainerCargoTonnes": "ContainerCargoTonnes",
    "ContainerVolumeTEU": "ContainerVolumeTEU",
    # Revenue per TEU (OOCL/Orient Overseas, 0316.HK): the freight-rate yield
    # behind a container carrier's revenue line. Volume (ContainerVolumeTEU)
    # times this yield is the revenue story, and neither factor is
    # derivable from the blended Revenue column alone.
    "RevenuePerTEU": "RevenuePerTEU",
    "NormalisedNPAT": "NormalisedNPAT",
    # Cinema software vendor's SaaS transition and two-segment split
    # (VGL.NZ). SaaS/Recurring/NonRecurring revenue is the company's own
    # disclosure of the on-prem-license-to-cloud migration, and the
    # Cinema/Film segment split is ~80/20 of FY2025 revenue -- neither is
    # visible in the consolidated Revenue line. ContributionMargin is
    # management's own operating-leverage measure below gross profit.
    "SaaSRevenue": "SaaSRevenue",
    "RecurringRevenue": "RecurringRevenue",
    "NonRecurringRevenue": "NonRecurringRevenue",
    "CinemaSegmentRevenue": "CinemaSegmentRevenue",
    "FilmSegmentRevenue": "FilmSegmentRevenue",
    "ContributionMargin": "ContributionMargin",
    # Bricks-and-mortar retailer channel mix and like-for-like growth
    # (The Warehouse Group, WHS.NZ). Online sales as a percentage of group
    # sales and same-store sales growth are the two numbers that separate
    # genuine demand growth from a changing store footprint (StoreCount
    # fell 88 -> 85 over FY2024-FY2025).
    "OnlineSalesPct": "OnlineSalesPct",
    "SameStoreSalesGrowth": "SameStoreSalesGrowth",
    # Global packaging group's two-segment split and non-GAAP measures
    # (Amcor plc, AMC.AX). Flexibles and Rigid Packaging are the company's
    # two reporting segments. After the Berry Global merger, GAAP EBIT/
    # EBITDA/Net Income/FCF are depressed by integration and transaction
    # costs, so management's own Adjusted measures -- not the statutory
    # figures -- are what run-rate profitability is judged on.
    "SegmentRevenueFlexibles": "SegmentRevenueFlexibles",
    "SegmentRevenueRigidPackaging": "SegmentRevenueRigidPackaging",
    "AdjustedEBIT": "AdjustedEBIT",
    "AdjustedEBITDA": "AdjustedEBITDA",
    "AdjustedNetIncome": "AdjustedNetIncome",
    "AdjustedFreeCashFlow": "AdjustedFreeCashFlow",
    # Stapled-security energy infrastructure operator (APA Group, APA.AX).
    # DistributionPerSecurity and FreeCashFlowPerSecurity are the per-
    # security cash measures income investors actually track, distinct
    # from statutory EPS since APA distributes more than statutory
    # earnings. NetIncomeExSignificant strips one-off gains/impairments
    # (e.g. the H1 FY2024 $1,051m Goldfields Gas Pipeline remeasurement
    # gain) that otherwise swamp statutory net income. RevenueExPassThrough
    # excludes pass-through gas/electricity costs recovered dollar-for-
    # dollar from customers. TotalDrawnDebt is APA's own gearing metric
    # (nets deferred borrowing costs and FX), the figure it quotes for
    # leverage rather than the raw balance-sheet borrowings field.
    "DistributionPerSecurity": "DistributionPerSecurity",
    "FreeCashFlowPerSecurity": "FreeCashFlowPerSecurity",
    "NetIncomeExSignificant": "NetIncomeExSignificant",
    "RevenueExPassThrough": "RevenueExPassThrough",
    "TotalDrawnDebt": "TotalDrawnDebt",
    # Canadian midstream pipeline operator (Pembina Pipeline Corporation,
    # PPL.TO). TotalVolumes (mboe/d) is pipeline/facility throughput, the
    # physical operating driver distinct from financial Revenue, which also
    # moves with commodity marketing. AdjustedCashFlowFromOperations is
    # Pembina's own disclosed non-GAAP operating cash flow measure,
    # distinct from the GAAP OperatingCashFlow line.
    "TotalVolumes": "TotalVolumes",
    "AdjustedCashFlowFromOperations": "AdjustedCashFlowFromOperations",
    # Market-infrastructure operator (ASX Limited, ASX.AX). The four
    # reporting segments -- Listings, Markets, Securities & Payments, and
    # Technology & Data -- are what the revenue mix story is told through,
    # not a single blended Revenue line. TotalCashMarketTrades/Value,
    # TotalFuturesContracts, NewListings and ListedEntities are ASX's own
    # disclosed operating-activity volumes (Chairman's report/investor
    # presentation), the throughput drivers behind the trading and listing
    # fee lines -- not something derivable from the financial statements.
    "SegmentRevenueListings": "SegmentRevenueListings",
    "SegmentRevenueMarkets": "SegmentRevenueMarkets",
    "SegmentRevenueSecuritiesPayments": "SegmentRevenueSecuritiesPayments",
    "SegmentRevenueTechnologyData": "SegmentRevenueTechnologyData",
    "TotalCashMarketTrades": "TotalCashMarketTrades",
    "TotalCashMarketValue": "TotalCashMarketValue",
    "TotalFuturesContracts": "TotalFuturesContracts",
    "NewListings": "NewListings",
    "ListedEntities": "ListedEntities",
    # E&P reserve/production disclosures (Beach Energy, BPT.AX): a
    # depleting-reserve producer's forward path is driven by these, not by
    # the income statement -- 2P reserves nearly halved FY22->FY26 (283 ->
    # 156 MMboe) while Production held roughly flat, which is exactly the
    # divergence the reserve-life and contingent-resources columns exist to
    # show alongside it.
    "Production": "Production",
    "Reserves2P": "Reserves2P",
    "ContingentResources2C": "ContingentResources2C",
    "ReserveLifeYears": "ReserveLifeYears",
    "DividendsDeclaredPerShare": "DividendsDeclaredPerShare",
    # Diversified steel manufacturer (BlueScope Steel, BSL.AX): five
    # reporting segments -- Australian Steel Products, North Star BlueScope
    # (US), New Zealand & Pacific Steel, Building Products Asia/NA and
    # Coated Products Americas -- with North Star now the largest single
    # EBIT contributor. Only disclosed for FY2025/FY2026 so far. Despatch
    # volumes (kt) are the segments' own throughput measure, distinct from
    # the dollar revenue line. NetProfitAttributable excludes the ~10-15%
    # non-controlling interest that NetIncome includes; UnderlyingEBITROIC
    # is BlueScope's own return-on-invested-capital measure.
    "SegmentEBIT_AustralianSteelProducts": "SegmentEBIT_AustralianSteelProducts",
    "SegmentEBIT_NorthStarBlueScope": "SegmentEBIT_NorthStarBlueScope",
    "SegmentEBIT_NewZealandPacificSteel": "SegmentEBIT_NewZealandPacificSteel",
    "SegmentEBIT_BuildingProductsAsiaNA": "SegmentEBIT_BuildingProductsAsiaNA",
    "SegmentEBIT_CoatedProductsAmericas": "SegmentEBIT_CoatedProductsAmericas",
    "Despatches_AustralianSteelProducts": "Despatches_AustralianSteelProducts",
    "Despatches_NorthStarBlueScope": "Despatches_NorthStarBlueScope",
    "Despatches_BuildingProductsAsiaNA": "Despatches_BuildingProductsAsiaNA",
    "Despatches_CoatedProductsAmericas": "Despatches_CoatedProductsAmericas",
    "NetProfitAttributable": "NetProfitAttributable",
    "UnderlyingEBITROIC": "UnderlyingEBITROIC",
    # Residential land/property developer volume measures (Cedar Woods
    # Properties, CWP.AX): unlike a REIT this business recognises revenue on
    # settlement, not accrual, so lot/unit counts and the presold-but-not-
    # yet-settled dollar book are the forward indicators management and the
    # market watch -- Presales ($830m at 30 June 2026) is the single biggest
    # forward-revenue-coverage number in the FY26 result.
    "NetSalesLots": "NetSalesLots",
    "Presales": "Presales",
    "Settlements": "Settlements",
    # REIT net tangible assets per security (Dexus, DXS.AX and similar):
    # the traded price's discount/premium to NTA per security is the
    # valuation anchor for stapled property trusts, reported every
    # half-year alongside the interim/annual result.
    "NetTangibleAssetsPerSecurity": "NetTangibleAssetsPerSecurity",
    # Four-banner specialty retailer segment split (Super Retail Group,
    # SUL.AX): Supercheap Auto, Rebel, BCF and Macpac each disclose their
    # own revenue and EBITDA, plus an Australia/New Zealand geographic
    # split. FY26 diverges sharply by banner (BCF weak on algae-bloom/
    # coastal disruption, Macpac hit by a mild winter, SCA/Rebel solid) --
    # a story the consolidated totals hide.
    "SegmentRevenue_SCA": "SegmentRevenue_SCA",
    "SegmentRevenue_Rebel": "SegmentRevenue_Rebel",
    "SegmentRevenue_BCF": "SegmentRevenue_BCF",
    "SegmentRevenue_Macpac": "SegmentRevenue_Macpac",
    "SegmentEBITDA_SCA": "SegmentEBITDA_SCA",
    "SegmentEBITDA_Rebel": "SegmentEBITDA_Rebel",
    "SegmentEBITDA_BCF": "SegmentEBITDA_BCF",
    "SegmentEBITDA_Macpac": "SegmentEBITDA_Macpac",
    "RevenueAustralia": "RevenueAustralia",
    "RevenueNewZealand": "RevenueNewZealand",
    # One-off return-of-capital distributions (Wesfarmers, WES.AX): unlike
    # ordinary dividends, these are return-of-capital transactions to
    # shareholders (A$2,267m in FY2022, A$1,249m in FY2026) that explain why
    # book-value/equity CAGR looks negative despite healthy earnings.
    "CapitalReturn": "CapitalReturn",
    # LIC income-statement components (Barramundi Limited, BRM.NZ, and other
    # Fisher Funds-managed listed investment companies): a LIC's "revenue" is
    # really three distinct sources -- dividend income received from the
    # portfolio, the unrealised/realised net change in fair value of
    # investments (the dominant, volatile swing line), and cash proceeds
    # from BRMWI warrant exercises (a financing inflow, not income). Blending
    # them into one Revenue figure hides which of the three moved.
    "DividendIncome": "DividendIncome",
    "NetChangeInFairValue": "NetChangeInFairValue",
    "WarrantExerciseProceeds": "WarrantExerciseProceeds",
    # Cyclone Gabrielle insurance proceeds distorting operating cash flow
    # (Bremworth Limited, BRW.NZ): $42.2m of business-interruption/asset
    # insurance proceeds landed in FY2025 operating cash flow, making that
    # year's statutory OCF/EBITDA read as a strong recovery when the
    # underlying carpet-manufacturing business was still loss-making
    # (normalised FY2025 EBITDA was -$13.2m). ExInsurance is the cleaner
    # comparable series; CashInOCF is the one-off itself.
    "CycloneInsuranceCashInOCF": "CycloneInsuranceCashInOCF",
    "OperatingCashFlowExInsurance": "OperatingCashFlowExInsurance",
    "Inventory": "Inventory",
    # Dealership funding structure (Colonial Motor Company, CMO.NZ): the group
    # runs its own internal finance function for vehicle inventory --
    # floorplan finance is a revolving, self-liquidating facility tied
    # directly to stock on hand, not general borrowings, and at-call deposits
    # are the retail funding side of that book. Blending either into
    # TotalDebt/CashAndEquivalents hides how much of group funding is
    # floorplan-specific versus core leverage.
    "VehicleFloorplanFinance": "VehicleFloorplanFinance",
    "AtCallDeposits": "AtCallDeposits",
    # Non-controlling interest in profit (CMO.NZ): partly-owned dealership
    # subsidiaries -- including the BYD joint-venture dealerships -- mean
    # reported NetIncome is not all attributable to CMO shareholders; this is
    # the minority share carved out of group NPAT.
    "NonControllingInterestProfit": "NonControllingInterestProfit",
    # FY2026 goodwill/other-asset impairment on the North American CGU
    # (EROAD, ERD.NZ): NZ$134.7m, dominating reported NetIncome that year
    # (a swing to -$161.1m). NormalisedEBIT is management's own non-GAAP
    # operating measure used throughout their results presentations,
    # distinct from the statutory OperatingIncome column.
    "ImpairmentGoodwillAndOtherAssets": "ImpairmentGoodwillAndOtherAssets",
    "NormalisedEBIT": "NormalisedEBIT",
    # Cash interest paid (Fonterra Co-operative Group, FCG.NZ): the
    # co-operative's own deleveraging story is told in cash interest, not
    # the statutory net-margin line -- interest paid fell from ~$446m
    # FY2018 to ~$202m FY2026 as debt was paid down, which is invisible in
    # the generic TotalDebt/CashAndEquivalents pair alone.
    "InterestPaid": "InterestPaid",
    # Cash generated from operations (SUNeVision Holdings, 1686.HK): the
    # debt-funded data-centre build-out means interest paid (~HK$10m FY2017
    # to >HK$800m FY2025) is a growing drag between operating cash and
    # OperatingCashFlow -- CGFO is the pre-interest, pre-tax cash line that
    # makes the coverage ratio visible, and is not derivable from the
    # already-net OperatingCashFlow column.
    "CashGeneratedFromOperations": "CashGeneratedFromOperations",
    # Amortisation and term deposits (ikeGPS Group, IKE.NZ): Amortisation is
    # Depreciation's smaller companion in D&A but is a distinct concept, not
    # an alias of it, and is not an owner-FCF component -- it belongs
    # promoted, not blocked. TermDeposits is material: the DCF's net-cash
    # figure is cash-and-equivalents PLUS term deposits (~NZ$21.1m of the
    # ~NZ$32.8m FY2026 net cash), so leaving it in `kpis` understated cash
    # on the dashboard by roughly two-thirds.
    "Amortisation": "Amortisation",
    "TermDeposits": "TermDeposits",
    # Cash-and-equivalents split (Keymed Biosciences, 2162.HK): the
    # financial-parser combined CashOnly and TermDeposits into the single
    # CashAndEquivalents column, so CashOnly on its own was stranded in
    # `kpis` with no way to see the split (H1 FY2026: CNY2,339.4m cash-only
    # vs CNY556.1m term deposits) -- promoted alongside TermDeposits above so
    # the composition is chartable, not just the combined total.
    "CashOnly": "CashOnly",
    # Wealth-management products at fair value through P&L (Haitian
    # International Holdings, 1882.HK): a second treasury line alongside
    # TermDeposits, ~RMB10.2bn at H1 FY2026 (~25% of the DCF's weighted
    # intrinsic value) and valued separately in the equity_bridge with a 10%
    # haircut -- leaving it in `kpis` hid roughly a quarter of the bridge from
    # the dashboard.
    "FVTPLInvestments": "FVTPLInvestments",
    # Investment-holding company whose equity-accounted JV/associate
    # portfolio IS the business (Power Assets Holdings, 0006.HK): statutory
    # Revenue is ~85% understated (it is mostly shareholder-loan interest
    # and excludes JV/associate dividends entirely), so ShareOfJVProfit/
    # ShareOfAssociateProfit (equity-accounted earnings) and the actual cash
    # received -- DividendsReceivedJV, DividendsReceivedAssociates,
    # DistributionFromAssociate, LoanInterestIncomeJVAssociates -- are what
    # the DCF's base_revenue is built from (see DCF JSON inputs.
    # base_revenue_basis), not the CSV's Revenue column. GainOnDisposalJV
    # is the one-off ~HK$11.3bn UK Power Networks sale gain booked in H1
    # FY2026, which must be flagged as non-recurring rather than read as
    # organic earnings growth.
    "ShareOfJVProfit": "ShareOfJVProfit",
    "ShareOfAssociateProfit": "ShareOfAssociateProfit",
    "DividendsReceivedJV": "DividendsReceivedJV",
    "DividendsReceivedAssociates": "DividendsReceivedAssociates",
    "DistributionFromAssociate": "DistributionFromAssociate",
    "LoanInterestIncomeJVAssociates": "LoanInterestIncomeJVAssociates",
    "GainOnDisposalJV": "GainOnDisposalJV",
    # Core trading revenue vs investment income (Hong Kong Exchanges and
    # Clearing, 0388.HK): the statutory Revenue line blends the exchange's
    # own trading/clearing/listing fees with investment income earned on
    # clearing-participant margin money, which swings with interest rates
    # independent of trading activity (GrossInvestmentIncome went from
    # HK$1.35bn in FY2021 to HK$10.97bn in FY2023 on rate hikes alone).
    # PrincipalOperatingCashFlow is HKEX's own disclosed cash-flow measure
    # that excludes movements in participant margin/clearing balances the
    # statutory OperatingCashFlow column does not separate out.
    "CoreRevenue": "CoreRevenue",
    "GrossInvestmentIncome": "GrossInvestmentIncome",
    "PrincipalOperatingCashFlow": "PrincipalOperatingCashFlow",
    # Leverage covenant measure and disclosed patient-impact metric (Demant
    # A/S, DEMANT.CO): gearing (net interest-bearing debt / EBITDA) is the
    # company's own covenant ratio -- ~3.0x at H1 FY2026 against a 2.0-2.5x
    # target -- distinct from the gross TotalDebt column, which nets nothing.
    # ProfitContinuingOperations is the comparable earnings line to
    # Revenue/OperatingIncome (both continuing-ops restated), since reported
    # NetIncome still includes discontinued operations. Acquisitions cash
    # outflow explains the FY2025 gearing spike (~6.3bn DKK deal year).
    "net_interest_bearing_debt": "NetInterestBearingDebt",
    "gearing_multiple": "GearingMultiple",
    "lives_improved_million": "LivesImprovedMillion",
    "profit_continuing_operations": "ProfitContinuingOperations",
    "acquisitions": "Acquisitions",
    # Non-GAAP "Core" EPS (AstraZeneca, AZN): management's own adjusted
    # earnings measure, distinct from the Reported/IFRS EPS in core_metrics.
    # Populated FY2019-FY2021 and H1/Q3/Q1 from FY2021 on -- not every period,
    # so the chart must tolerate gaps rather than assume full history.
    "CoreEPS": "CoreEPS",
    # Licensing/milestone intangible-asset purchases (AZN): a distinct
    # pharma capital-allocation line from PP&E CapEx, disclosed separately
    # in the cash flow statement every period since FY2015.
    "PurchaseOfIntangibles": "PurchaseOfIntangibles",
    # Cash settlement of employee share awards (AZN): a financing/SBC-
    # adjacent cash outflow disclosed alongside the equity award programme.
    "ShareAwardSettlement": "ShareAwardSettlement",
    # Total group profit vs NCI share (BHP Group, BHP): core_metrics/CSV
    # NetIncome is profit attributable to BHP shareholders only.
    # ProfitAfterTaxTotal is BEFORE the ~42.5% Escondida (and other JV)
    # minority partners' share is carved out, so the gap between the two is
    # earnings BHP consolidates but does not itself own. DiscontinuedOps-
    # ProfitPetroleum is the one-off ~$10.7bn FY2022 gain on the Woodside
    # petroleum merger, disclosed only that year -- without it FY2022 looks
    # like organic earnings growth rather than a merger gain.
    "ProfitAfterTaxTotal": "ProfitAfterTaxTotal",
    "ProfitAttributableNCI": "ProfitAttributableNCI",
    "DiscontinuedOpsProfitPetroleum": "DiscontinuedOpsProfitPetroleum",
    # Equity attributable to common shareholders only (Bank of Nova Scotia,
    # BNS): core_metrics/CSV ShareholdersEquity includes preferred shares and
    # other equity instruments, so it overstates the basis for book-value-
    # per-common-share and ROE-on-common -- the measures a bank's own
    # disclosures and peer comparisons are built on.
    "TotalCommonEquity": "TotalCommonEquity",
    # CIBC (CM): the two core bank profitability KPIs management guides to
    # each quarter. EfficiencyRatio (non-interest expense / revenue) is the
    # cost-discipline measure; PrePCLProfit (pre-provision, pre-credit-loss
    # profit) is earnings power before the credit cycle shows up, distinct
    # from both the core_metrics NetIncome column and the CreditImpairment-
    # Charge KPI already promoted.
    "EfficiencyRatio": "EfficiencyRatio",
    "PrePCLProfit": "PrePCLProfit",
    # Smurfit WestRock plc (SKG.L): FY2024 and FY2025 did not disclose a
    # full-group Adjusted EBITDA, only segment-level Adjusted EBITDA before
    # head-office costs. Distinct from the generic AdjustedEBITDA entry
    # above (whole-company basis) -- reusing that name would overstate
    # group profitability by the head-office cost excluded here.
    "SegmentAdjustedEBITDA": "SegmentAdjustedEBITDA",
    # Alimentation Couche-Tard Inc. (ATD.TO): network scale and JV earnings.
    # TotalNetworkSites is the company's own disclosed count of
    # convenience/fuel sites across its global network (company-operated
    # plus franchised/affiliated) -- a retail rollup's real scale metric,
    # distinct from the sparsely-populated core_metrics StoreCount.
    # JVShareOfEarnings is the share of joint-venture earnings the
    # financial-parser folds into the company's EBITDA definition.
    "TotalNetworkSites": "TotalNetworkSites",
    "JVShareOfEarnings": "JVShareOfEarnings",
    # BCE Inc. (BCE.TO): core wireless unit economics, disclosed every
    # quarter alongside the already-promoted BroadbandConnections/
    # ChurnRate/Subscribers. MobileARPU is the per-subscriber monthly
    # revenue that drives the wireless segment's top line independent of
    # subscriber count; MobileNetAdds is the quarterly net change in mobile
    # phone subscribers (growth momentum, distinct from the EOP Subscribers
    # stock); IPTVSubscribers is the home-services bundle's third leg
    # alongside broadband and mobile.
    "MobileARPU": "MobileARPU",
    "MobileNetAdds": "MobileNetAdds",
    "IPTVSubscribers": "IPTVSubscribers",
    # Railroad operating metrics (Canadian National Railway, CNR.TO):
    # Operating Ratio (operating expense / revenue, lower is better) is the
    # headline efficiency metric the whole rail industry is judged on, and
    # the "Adjusted" variant strips out significant items management
    # excludes. Carloads/GrossTonMiles/RevenueTonMiles are volume/traffic
    # metrics distinct from dollar Revenue; FreightRevenuePerRTM is the
    # yield (revenue efficiency per unit of traffic) behind that volume.
    # EmployeesEndOfPeriod is headcount, a labour-productivity input.
    "OperatingRatio": "OperatingRatio",
    "AdjustedOperatingRatio": "AdjustedOperatingRatio",
    "Carloads": "Carloads",
    "GrossTonMiles": "GrossTonMiles",
    "RevenueTonMiles": "RevenueTonMiles",
    "FreightRevenuePerRTM": "FreightRevenuePerRTM",
    "EmployeesEndOfPeriod": "EmployeesEndOfPeriod",
    # Rogers Communications (RCI-B.TO): segment revenue across its three
    # reporting segments (Wireless, Cable, Media) -- a single blended
    # Revenue column hides which segment drives growth, and Media jumped
    # ~32% FY2024->FY2025 on sports-rights timing while Wireless grew ~1%.
    # BlendedARPU/BlendedABPU are predecessor definitions of the
    # already-promoted MobileARPU: Rogers reported "Blended ARPU" through
    # FY2017, redefined it as "Blended ABPU" (Average Billing Per User, adds
    # device financing) for FY2018-2021, then switched to "Mobile ARPU" from
    # FY2022 -- three non-comparable series, not one continuous line.
    # InternetNetAdds is the annual net change in broadband subscribers,
    # alongside the already-promoted BroadbandConnections stock.
    "SegmentRevenueWireless": "SegmentRevenueWireless",
    "SegmentRevenueCable": "SegmentRevenueCable",
    "SegmentRevenueMedia": "SegmentRevenueMedia",
    "BlendedARPU": "BlendedARPU",
    "BlendedABPU": "BlendedABPU",
    "InternetNetAdds": "InternetNetAdds",
    # Gas utility operating scale (The Hong Kong and China Gas Company,
    # 0003.HK / Towngas): CustomersHK and MainlandCityGasCustomers
    # (thousands) are the HK and mainland city-gas customer bases;
    # TownGasSoldHK (million MJ) and MainlandGasSold (million m3) are the
    # physical volumes sold -- different units because HK town gas is
    # measured on a calorific-value basis and the mainland JVs sell natural
    # gas volumetrically. EmployeesHKCompany is HK-company-only headcount.
    # DilutedEPS sits alongside the core_metrics EPS column on a different
    # share-count basis. Only FY2023-FY2025 were extracted (earlier years
    # were chart-image only in the filings).
    "CustomersHK": "CustomersHK",
    "MainlandCityGasCustomers": "MainlandCityGasCustomers",
    "MainlandGasSold": "MainlandGasSold",
    "TownGasSoldHK": "TownGasSoldHK",
    "EmployeesHKCompany": "EmployeesHKCompany",
    "DilutedEPS": "DilutedEPS",
    # Rail-plus-Property segment split and recurrent-vs-property-development
    # profit bridge (MTR Corporation, 0066.HK): the four revenue lines (HK
    # Transport, Station Commercial, HK Property Rental, Mainland &
    # International) sum close to total Revenue but move on different
    # drivers (fare days vs retail rent vs offshore rail concessions).
    # RecurrentBusinessProfit/RecurrentEBITDA exclude property development
    # entirely, which is lumpy project-recognition income that now dominates
    # net income (HK$12.2bn of HK$15.9bn H1 FY2026 net profit) -- the DCF's
    # sum-of-parts split rests on this line, not on consolidated NetIncome.
    # PropertyDevelopmentReceipts is cash received from sold units (can lead
    # or lag the P&L recognition in HKPropertyDevelopmentProfitPreTax /
    # PropertyDevelopmentProfitAttributable). VariableAndFixedAnnualPayments
    # is the annual payment to KCRC under the Service Concession Agreement
    # (~6% of revenue) -- a fixed claim on cash flow before it reaches
    # shareholders. PerpetualCapitalSecurities is a hybrid instrument
    # classified in equity (so it inflates TotalEquityInclHybridNCI versus
    # ShareholdersEquity) but carries a coupon like debt.
    "RevenueHKTransport": "RevenueHKTransport",
    "RevenueStationCommercial": "RevenueStationCommercial",
    "RevenueHKPropertyRental": "RevenueHKPropertyRental",
    "RevenueMainlandInternational": "RevenueMainlandInternational",
    "HKPropertyDevelopmentProfitPreTax": "HKPropertyDevelopmentProfitPreTax",
    "PropertyDevelopmentProfitAttributable": "PropertyDevelopmentProfitAttributable",
    "RecurrentBusinessProfit": "RecurrentBusinessProfit",
    "RecurrentEBITDA": "RecurrentEBITDA",
    "PropertyDevelopmentReceipts": "PropertyDevelopmentReceipts",
    "VariableAndFixedAnnualPayments": "VariableAndFixedAnnualPayments",
    "PerpetualCapitalSecurities": "PerpetualCapitalSecurities",
    # Kunlun Energy Company Limited (0135.HK): minority shareholders in
    # part-owned subsidiaries take a large and growing share of consolidated
    # profit (~37% in FY2025) -- ProfitForPeriodInclNCI (group, before the
    # minority cut) sits well above the attributable NetIncome column, the
    # same minority-interest story as PCCW/ATCO above but under this
    # company's own disclosed label. CashEquivalentsOnly and
    # TimeDepositsOver3M split out how much of the CashAndEquivalents total
    # is actually demand cash versus time deposits placed for 3+ months
    # (FY2025: 13,329 of 44,609) -- the latter is not available on demand,
    # so lumping them together overstates near-term liquidity.
    "ProfitForPeriodInclNCI": "ProfitForPeriodInclNCI",
    "CashEquivalentsOnly": "CashEquivalentsOnly",
    "TimeDepositsOver3M": "TimeDepositsOver3M",
    # China Ruyi Holdings (0136.HK): reported operating profit is dominated
    # by "Other gains/(losses) -- net", fair-value marks on a large
    # investment book (FY2024 loss RMB1,222.0m, FY2025 gain RMB1,281.7m)
    # that swing reported NetIncome from -190.5m to +1,796.6m -- the DCF
    # strips this out entirely to reach the underlying film/TV/streaming/
    # games operating economics. AdjustedNetProfit is the company's own
    # non-GAAP measure excluding that swing; FilmRightsAmortisation is the
    # content amortisation cost the DCF explicitly does NOT add back.
    "OtherGainsNet": "OtherGainsNet",
    "AdjustedNetProfit": "AdjustedNetProfit",
    "FilmRightsAmortisation": "FilmRightsAmortisation",
    # Sinofert Holdings (0297.HK): time deposits sit alongside cash and
    # equivalents on the balance sheet and are comparable in size to that
    # line (FY2025: RMB3,269.0m deposits vs RMB2,928.3m cash) -- the
    # dcf-analyst's net-cash bridge explicitly sums cash + time deposits to
    # reach the DCF's net_debt figure, so CashAndEquivalents alone
    # understates the liquid balance by roughly half.
    "TimeDeposits": "TimeDeposits",
    # China East Education Holdings (0667.HK): FVTPLFinancialAssets is
    # non-principal-protected structured deposits and level-3 fund
    # investments the dcf-analyst sums into net cash alongside
    # CashAndEquivalents and TimeDeposits (30 Jun 2026: RMB1,488.0m of the
    # RMB3,515.3m net-cash bridge) -- material enough that
    # CashAndEquivalents alone understates the liquid balance by roughly
    # 40%. LeasePrincipalRepaid is the IFRS-16 financing-cash-flow line
    # behind the DCF's lease treatment (full lease cash payment, principal
    # + interest, charged inside EBIT rather than treated as debt service).
    "FVTPLFinancialAssets": "FVTPLFinancialAssets",
    "LeasePrincipalRepaid": "LeasePrincipalRepaid",
    # DPC Dash (1405.HK): LeaseInterestPaid is the interest half of the
    # IFRS-16 financing-cash-flow lease payment, alongside the already-
    # promoted LeasePrincipalRepaid -- together they are the full lease cash
    # burden the DCF deducts from reported FCF to get Owner FCF (negative
    # every year FY2022-25).
    "LeaseInterestPaid": "LeaseInterestPaid",
    # Shanghai Industrial Holdings (0363.HK): the infrastructure segment's
    # toll-road concession purchases and the property segment's investment-
    # property additions are the reinvestment lines the DCF's bear/base/bull
    # split turns on -- the Hu-Yu, Jing-Hu and Hu-Kun expressway concessions
    # expire 2027-2030, and PurchaseOfOperatingConcessions is the cash spent
    # renewing or extending that concession book, not derivable from the
    # blended CapEx column.
    "InvestmentPropertyCapex": "InvestmentPropertyCapex",
    "PurchaseOfOperatingConcessions": "PurchaseOfOperatingConcessions",
    # China Overseas Land & Investment (0688.HK): cash added to the
    # held-for-rent investment-property book at fair value (offices, malls,
    # business parks) -- the lead indicator for whether the REIT-recyclable
    # portfolio the bull case leans on to help close the 0.32x price-to-book
    # gap is still growing or being run down. Distinct from
    # InvestmentPropertyCapex, which is a cost-basis capex spend line.
    "InvestmentPropertyAdditions": "InvestmentPropertyAdditions",
    # China Railway Group (0390.HK): a Chinese SOE infrastructure contractor
    # consolidates large PPP/infrastructure subsidiaries, so TotalEquity and
    # ProfitTotal (both including minority interests) sit well above the
    # owners'-only ShareholdersEquity and NetIncome columns -- FY2025
    # TotalEquity RMB540,450m vs ShareholdersEquity RMB370,915m. Within
    # ShareholdersEquity, PerpetualNotes (RMB52,794m at FY2025) are
    # equity-classified under IFRS but economically debt-like (fixed coupon,
    # callable), so owners' *ordinary* equity is smaller still. None of the
    # three is derivable from the existing CSV columns.
    "TotalEquity": "TotalEquity",
    "ProfitTotal": "ProfitTotal",
    "PerpetualNotes": "PerpetualNotes",
    # China Jinmao Holdings (0817.HK): another SOE developer with the same
    # NCI/perpetual-instrument gap as China Railway Group above --
    # ProfitIncludingNCI sits well above NetIncome (attributable) because
    # ~55% of total equity is non-controlling interests, and a RMB15.2bn
    # Sinochem HK perpetual instrument has sat inside equity attributable to
    # owners since H1 FY2024. LandAppreciationTaxPaid is the real-estate-
    # specific levy on development-property disposals, not derivable from
    # the generic CashTaxesPaid line.
    "ProfitIncludingNCI": "ProfitIncludingNCI",
    "LandAppreciationTaxPaid": "LandAppreciationTaxPaid",
    # Hisense Home Appliances Group (0921.HK): the Hisense-Hitachi HVAC JV is
    # only ~49-51% owned, so NetProfitInclMinority (total group net profit)
    # sits well above attributable NetIncome, with MinorityInterests (the
    # balance-sheet NCI equity) and MinorityDividendsPaid (cash-flow) as the
    # counterparts -- the same attributable-vs-total gap as ProfitIncludingNCI
    # above but a distinct disclosure line, not an alias of it.
    # TradingFinancialAssets (wealth-management/treasury products, larger
    # than operating cash) is the balance-sheet pile the DCF applies a
    # trapped-capital discount to.
    "NetProfitInclMinority": "NetProfitInclMinority",
    "MinorityInterests": "MinorityInterests",
    "MinorityDividendsPaid": "MinorityDividendsPaid",
    "TradingFinancialAssets": "TradingFinancialAssets",
    # Brilliance China Automotive Holdings (1114.HK): a holding company whose
    # consolidated Revenue is tiny -- the real earnings engine is the 25%
    # equity-accounted stake in the BMW Brilliance Automotive JV.
    # ShareOfAssociatesAndJVResults is that JV/associate income line, the
    # overwhelming majority of net income here; distinct from Henderson
    # Land's split ShareOfAssociatesProfit/ShareOfJointVenturesProfit and
    # China Merchants Port's combined ShareOfAssocJVProfit above because this
    # filing discloses it under its own name. ProfitInclNCI is the same
    # attributable-vs-total gap as ProfitIncludingNCI/NetProfitInclMinority
    # above but this filing's own disclosed line.
    "ShareOfAssociatesAndJVResults": "ShareOfAssociatesAndJVResults",
    "ProfitInclNCI": "ProfitInclNCI",
    # Tencent Holdings (0700.HK): the three-segment revenue split --
    # Value-Added Services (gaming + social networks, ~49% of FY2025
    # revenue), Marketing Services (advertising, ~19%), FinTech & Business
    # Services (payments/wealth/lending/cloud, ~31%), and the small Others
    # residual -- has very different growth and margin profiles per segment
    # (FinTech & Business Services and Marketing Services both grew faster
    # than VAS through FY2024-FY2025). A single blended Revenue column
    # hides that mix shift. Populated in `kpis` back to FY2018/FY2019 but
    # absent from PROMOTE_KPIS. OperatingIncomeNewBasis is deliberately not
    # promoted here: the financial-parser noted it is a restated basis
    # (post-FY2023 reclassification) not comparable to the legacy
    # OperatingIncome column already charted.
    "SegmentRevenueVAS": "SegmentRevenueVAS",
    "SegmentRevenueMarketingServices": "SegmentRevenueMarketingServices",
    "SegmentRevenueFinTechBusinessServices": "SegmentRevenueFinTechBusinessServices",
    "SegmentRevenueOthers": "SegmentRevenueOthers",
    # China Medical System Holdings (0867.HK): cash paid to acquire
    # in-licensed drug product rights -- the dcf-analyst treats it as
    # mandatory reinvestment, deducted from owner FCF every year, because
    # the growth model depends on continuously buying licences (RMB423-473m
    # /yr in FY2024-25, RMB468m in H1 FY2026 alone). Full history back to
    # FY2017 in `kpis` but no CSV column, so the spend was invisible on the
    # dashboard. NetIncomeIncludingNCI is profit including non-controlling
    # interests, alongside the already-charted NetIncome (attributable).
    "IntangibleAssetPurchases": "IntangibleAssetPurchases",
    "NetIncomeIncludingNCI": "NetIncomeIncludingNCI",
    # China Construction Bank Corporation (0939.HK): the non-performing-loan
    # ratio and total capital adequacy ratio are the asset-quality and
    # solvency metrics a bank investor checks first, alongside the already
    # -promoted NIM/CET1Ratio/ROE. GrossInterestIncome is interest income
    # before netting against interest expense -- a distinct disclosure line
    # from the already-charted NetInterestIncome, not derivable from it
    # without also knowing interest expense.
    "NPLRatio": "NPLRatio",
    "TotalCapitalRatio": "TotalCapitalRatio",
    "GrossInterestIncome": "GrossInterestIncome",
    # China CITIC Bank Corporation (0998.HK): AllowanceCoverageRatio
    # (loan-loss reserves / NPLs) and CreditCost (impairment charge / average
    # loans, annualized) are the asset-quality pair asset-quality investors
    # read alongside NPLRatio -- a falling coverage ratio with a rising NPL
    # ratio is a reserve-adequacy warning the already-charted ratios alone
    # don't show. ROAA (net income / average assets) is the bank-standard
    # profitability measure that is comparable across banks of different
    # leverage, unlike ROE. NetFeeAndCommissionIncome is the non-interest
    # revenue line (wealth management, cards, settlement) that the bank
    # discloses separately from NetInterestIncome -- the diversification-away
    # -from-NIM-compression thesis needs it split out from blended Revenue.
    # OrdinaryShareholdersEquity excludes the RMB52.8bn+ of preference shares
    # and perpetual bonds sitting inside the already-charted ShareholdersEquity
    # column -- those carry fixed coupons and dilute the per-ordinary-share
    # book value the DCF and BookValuePerShare column are built on.
    "AllowanceCoverageRatio": "AllowanceCoverageRatio",
    "CreditCost": "CreditCost",
    "ROAA": "ROAA",
    "NetFeeAndCommissionIncome": "NetFeeAndCommissionIncome",
    "OrdinaryShareholdersEquity": "OrdinaryShareholdersEquity",
    # Seazen Group (1030.HK): consolidates a listed A-share subsidiary
    # (New Town Holdings, 601155.SS) it does not wholly own, so
    # NonControllingInterests is ~42% of total equity -- not a rounding
    # item -- yet had no CSV column separate from the already-charted
    # ShareholdersEquity (attributable only). SubsidiaryShareBuyback is
    # that subsidiary's own on-market repurchase, funded from its own
    # cash; tracked separately because it does not retire parent shares
    # or change the parent share count the DCF holds flat.
    "NonControllingInterests": "NonControllingInterests",
    "SubsidiaryShareBuyback": "SubsidiaryShareBuyback",
    # Sino Biopharmaceutical (1177.HK): minority interests take roughly
    # half of consolidated net profit via the NCI-heavy CT Tianqing
    # subsidiary plus the LaNova Medicines/Hygieia acquisitions.
    # DividendsPaidToNCI is the cash-flow counterpart to the
    # already-promoted NonControllingInterests balance -- the actual cash
    # leaving the group to minority shareholders.
    "DividendsPaidToNCI": "DividendsPaidToNCI",
    # TCL Electronics Holdings Limited (1070.HK): four-segment revenue split
    # -- large-display (TV sets, the core business), internet (smart-TV
    # advertising/content), innovative (other consumer electronics) and
    # photovoltaic (solar, a newer diversification) -- plus the volume and
    # share metrics management discloses alongside the blended Revenue
    # column: TV unit shipments, the Mini LED mix within those shipments,
    # and TV market share by both shipment volume and revenue (global
    # ranking claims). AdjustedNetIncomeAttributable strips one-offs from
    # the already-charted NetIncome; NetIncomeContinuingOps isolates
    # continuing operations in the one year a disposal was disclosed.
    "LargeDisplayRevenue": "LargeDisplayRevenue",
    "LargeDisplayGrossMargin": "LargeDisplayGrossMargin",
    "InternetRevenue": "InternetRevenue",
    "InternetGrossMargin": "InternetGrossMargin",
    "InnovativeBusinessRevenue": "InnovativeBusinessRevenue",
    "InnovativeBusinessGrossProfit": "InnovativeBusinessGrossProfit",
    "PhotovoltaicRevenue": "PhotovoltaicRevenue",
    "TVShipments": "TVShipments",
    "MiniLEDTVShipments": "MiniLEDTVShipments",
    "TVMarketShareByShipment": "TVMarketShareByShipment",
    "TVMarketShareByRevenue": "TVMarketShareByRevenue",
    "AdjustedNetIncomeAttributable": "AdjustedNetIncomeAttributable",
    "NetIncomeContinuingOps": "NetIncomeContinuingOps",
    # AIA Group Limited (1299.HK): EVEquity is AIA's disclosed Embedded
    # Value, the pan-Asian life insurer valuation anchor analysts and the
    # dcf-analyst cross-check against -- distinct from statutory
    # ShareholdersEquity, which does not capture the discounted value of
    # the in-force policy book. InterestRevenueOnFinancialAssets is
    # interest earned on the investment portfolio -- core operating
    # income for an insurer, deliberately kept separate from a stripped
    # non-operating interest line used elsewhere in the DCF.
    "EVEquity": "EVEquity",
    "InterestRevenueOnFinancialAssets": "InterestRevenueOnFinancialAssets",
    # Guming Holdings (1364.HK), tea-drink franchisor: AdjustedProfit is
    # management's own non-IFRS measure stripping the one-off non-cash fair
    # value gain/loss on preferred shares -- material in FY2025, where
    # reported NetIncome grew 108.6% YoY but AdjustedProfit grew only 66.9%
    # because of the RMB556.9m preferred-share fair-value gain booked at
    # listing. CupsSold is the operating unit-volume KPI (cups of tea sold)
    # behind GMV/StoreCount. FairValueChangePreferredShares is the one-off
    # gain itself, carried separately so it is visible as context rather
    # than buried inside NetIncome.
    "AdjustedProfit": "AdjustedProfit",
    "CupsSold": "CupsSold",
    "FairValueChangePreferredShares": "FairValueChangePreferredShares",
    # China Hongqiao Group Limited (1378.HK): EffectiveCashTaxRate (cash
    # taxes paid / profit before tax) swings 10.6%-48.8% across FY2018-25 --
    # an aluminium smelter with provincial tax incentives and swing items
    # that ProfitBeforeTax and CashTaxesPaid alone do not explain; not
    # derivable from other CSV columns since both inputs are DCF-only.
    "EffectiveCashTaxRate": "EffectiveCashTaxRate",
    # Tencent Music Entertainment Group (1698.HK): OnlineMusicRevenue grew
    # ~9.3bn -> ~26.7bn RMB FY2021-FY2025 while SocialEntertainmentRevenue
    # (live-streaming/virtual gifting) shrank ~19.8bn -> ~6.2bn under
    # Chinese regulatory pressure -- the two offsetting segment trends are
    # why total Revenue looked flat FY2022-FY2024 despite a fundamental
    # business-mix shift; not derivable from Revenue alone.
    "OnlineMusicRevenue": "OnlineMusicRevenue",
    "SocialEntertainmentRevenue": "SocialEntertainmentRevenue",
    # Time Interconnect Technology Limited (1729.HK): Luxshare Precision
    # Industry is both the controlling shareholder and, via
    # LoansFromRelatedCompanies, a lender -- the bank/related-party split of
    # TotalDebt is the leverage/dependency story (related-party loans grew
    # from ~parity with bank borrowings to over 3x by H1 FY2026), not
    # derivable from TotalDebt alone. SharePlacingProceeds and
    # SharesIssuedPeriodEnd capture the H1 FY2026 placings that lifted the
    # share count ~21% in one half-year.
    "BankBorrowings": "BankBorrowings",
    "LoansFromRelatedCompanies": "LoansFromRelatedCompanies",
    "SharePlacingProceeds": "SharePlacingProceeds",
    "SharesIssuedPeriodEnd": "SharesIssuedPeriodEnd",
    # Shandong Gold Mining (1787.HK): the CAS income statement folds
    # perpetual-bond-holder profit and minority interest into reported
    # NetIncome. PerpetualBonds (balance, CNY ~14bn FY2025) is deducted in
    # the DCF equity bridge as a debt-like claim senior to ordinary equity;
    # PerpetualBondProfit and MinorityInterestProfit are the two leakage
    # lines reconciling NetIncome to NetIncomeOrdinary (ex-perpetual-holder
    # profit). OperatingCashFlowCAS exists because the company switched
    # from IFRS to PRC GAAP (CAS) at FY2025 and OCF is not comparable
    # like-for-like across the switch (FY2024: RMB20.3bn IFRS vs RMB13.3bn
    # CAS for the same year).
    "PerpetualBonds": "PerpetualBonds",
    "PerpetualBondProfit": "PerpetualBondProfit",
    "MinorityInterestProfit": "MinorityInterestProfit",
    "NetIncomeOrdinary": "NetIncomeOrdinary",
    "OperatingCashFlowCAS": "OperatingCashFlowCAS",
    # Segment and product-line revenue split (Xiaomi Corporation, 1810.HK):
    # Smart EV only started contributing in FY2024 and reached its first
    # profitable quarter in FY2025 before swinging back to a segment loss in
    # H1 FY2026 -- the growth/risk story the blended Revenue column hides.
    # SmartphoneAIoTSegmentRevenue is the mature core business on the other
    # side of that split; SmartphoneRevenue/IoTLifestyleRevenue break the
    # legacy "Smartphone x AIoT" segment further into its two product lines.
    "SmartEVSegmentRevenue": "SmartEVSegmentRevenue",
    "SmartphoneAIoTSegmentRevenue": "SmartphoneAIoTSegmentRevenue",
    "SmartphoneRevenue": "SmartphoneRevenue",
    "IoTLifestyleRevenue": "IoTLifestyleRevenue",
    # Unit volumes behind the revenue lines above: smartphone shipments (FY
    # only, millions of units) and EV deliveries (FY2025+, vehicles) -- the
    # EV delivery run-rate against management's FY2026 target is a live
    # controversy per the qualitative analysis. ConnectedIoTDevices is the
    # ecosystem-scale metric (million units) disclosed alongside them.
    "SmartphoneShipments": "SmartphoneShipments",
    "EVDeliveries": "EVDeliveries",
    "ConnectedIoTDevices": "ConnectedIoTDevices",
    # Gold production volumes (Zhaojin Mining Industry, 1818.HK): own-mine
    # output (kg and the oz equivalent investors quote gold in) versus total
    # output including smelted/traded third-party gold -- a volume story
    # revenue alone cannot show since it also moves with the gold price.
    # Populated FY2018-FY2025 in `kpis` but absent from PROMOTE_KPIS.
    "GoldOutputMineProducedKg": "GoldOutputMineProducedKg",
    "GoldOutputMineProducedOz": "GoldOutputMineProducedOz",
    "GoldOutputTotalKg": "GoldOutputTotalKg",
    # Perpetual capital instruments (Zhaojin Mining Industry, 1818.HK):
    # RMB1.5-7.1bn of perpetual bonds classified in equity but debt-like in
    # substance -- they sit outside TotalDebt, and ShareholdersEquity-based
    # book value per ordinary share overstates what common shareholders
    # actually own. PerpetualDistributionsPaid is the cash cost of servicing
    # them, distinct from DividendsPaid to ordinary shareholders.
    "PerpetualCapitalInstruments": "PerpetualCapitalInstruments",
    "PerpetualDistributionsPaid": "PerpetualDistributionsPaid",
    # Embedded-value life-insurer disclosures (FWD Group Holdings, 1828.HK):
    # EmbeddedValue (ANW + VIF) is the standard EV-basis measure of what the
    # in-force book plus net assets are worth, distinct from book
    # ShareholdersEquity -- the DCF's cross-check section quotes EV/share
    # against the DCF's own intrinsic value. ValueOfNewBusiness and
    # NewBusinessCSM are the growth-engine metrics (new sales' contribution
    # to future profit); NewBusinessMargin is VNB/APE. SolvencyRatioLCSM is
    # the HK risk-based Local Capital Summation Method capital-adequacy
    # ratio -- it fell from 265% (FY2025) to 203% (H1 FY2026) per the
    # qualitative analysis, a material regulatory-buffer compression not
    # visible in any balance-sheet column.
    "EmbeddedValue": "EmbeddedValue",
    "ValueOfNewBusiness": "ValueOfNewBusiness",
    "NewBusinessCSM": "NewBusinessCSM",
    "NewBusinessMargin": "NewBusinessMargin",
    "SolvencyRatioLCSM": "SolvencyRatioLCSM",
    "APE": "APE",
    # Samsonite Group S.A. (1910.HK): a multi-brand, multi-region luggage
    # maker. Brand mix (Samsonite/Tumi/American Tourister), regional segment
    # mix (Asia/North America/Europe/Latin America) and wholesale-vs-DTC
    # channel mix are each a distinct investor question -- none derivable
    # from the blended Revenue column alone.
    "BrandRevenueSamsonite": "BrandRevenueSamsonite",
    "BrandRevenueTumi": "BrandRevenueTumi",
    "BrandRevenueAmericanTourister": "BrandRevenueAmericanTourister",
    "SegmentRevenueAsia": "SegmentRevenueAsia",
    "SegmentRevenueNorthAmerica": "SegmentRevenueNorthAmerica",
    "SegmentRevenueEurope": "SegmentRevenueEurope",
    "SegmentRevenueLatinAmerica": "SegmentRevenueLatinAmerica",
    "WholesaleRevenue": "WholesaleRevenue",
    "DTCRevenue": "DTCRevenue",
    # Chervon Holdings Limited (2285.HK): discloses no stock-based-
    # compensation expense line, so the dcf-analyst uses on-market share
    # purchases funding the employee share award scheme as the SBC-cost
    # proxy (0.3-0.4% of revenue in the model). Real valuation driver, not
    # derivable from StockBasedComp, which this ticker leaves blank.
    "ShareAwardSchemePurchases": "ShareAwardSchemePurchases",
    # Li Ning Company Limited (2331.HK): EPSDiluted is stored alongside
    # basic EPS. LeasePayments is the full lease cash payment
    # (principal+interest) -- distinct from the blocked LeasePrincipalRepayment
    # because this ticker has no bank debt (TotalDebt is lease liabilities
    # only) and the DCF treats lease cost as an operating expense rather than
    # debt service. SBCForfeitureReversal (reversals of stock-based comp for
    # forfeited awards) became large from FY2025 onward (167.9m FY2025,
    # 210.4m H1 FY2026) and is stored separately from gross StockBasedComp
    # specifically so it is never netted into it.
    "EPSDiluted": "EPSDiluted",
    "LeasePayments": "LeasePayments",
    "SBCForfeitureReversal": "SBCForfeitureReversal",
    # Sunny Optical Technology (2382.HK): the company restated its segment
    # categories from FY2025 onward (Handset/VehicleRelated/ARVR -> Handset/
    # Vehicle/XR/OtherProducts), so the two eras are stored under different
    # KPI names and never spliced together. HandsetRevenue (~63% of FY2025
    # revenue, mature/declining smartphone unit market), VehicleRevenue
    # (ADAS/smart-cabin growth driver) and XRRevenue (AR/VR optics, the
    # emerging driver) are the revenue-mix thesis the qualitative analysis
    # is built on and are not derivable from total Revenue alone.
    "HandsetRevenue": "HandsetRevenue",
    "VehicleRevenue": "VehicleRevenue",
    "XRRevenue": "XRRevenue",
    "OtherProductsRevenue": "OtherProductsRevenue",
    # BOC Hong Kong Holdings (2388.HK): AdvancesAndOtherAccounts is the loan
    # book (HK$1.28tn FY2018 to HK$1.81tn H1 FY2026) -- the standard bank
    # growth KPI, not derivable from TotalAssets, which also carries the
    # much larger investment-securities book. LoanToDepositRatio (69.3%
    # FY2022 down to 58.4% FY2025) is management's own disclosed
    # funding/liquidity ratio; deriving it from AdvancesAndOtherAccounts /
    # CustomerDeposits would silently diverge whenever the denominator
    # basis differs from the disclosed one.
    "AdvancesAndOtherAccounts": "AdvancesAndOtherAccounts",
    "LoanToDepositRatio": "LoanToDepositRatio",
    # HealthyWay Inc. (2587.HK): a pre-IPO redemption liability on
    # convertible-redeemable preferred shares drove the FY2023/FY2024
    # reported net losses (-RMB313.9m/-269.2m) almost entirely through
    # non-cash fair-value remeasurement -- pre-redemption PBT was actually
    # +RMB4m/+9m those years, and the liability was extinguished at the
    # Dec-2024 IPO (nil from FY2025). RedemptionLiabilityChange isolates
    # that non-operating charge against NetIncome/OperatingIncome.
    # NetIncomeAttributable is net income after minority interests, which
    # diverges from the total NetIncome column by several million per
    # period. Both populated in `kpis` but absent from PROMOTE_KPIS, so the
    # dashboard could not chart either.
    "RedemptionLiabilityChange": "RedemptionLiabilityChange",
    "NetIncomeAttributable": "NetIncomeAttributable",
}

# Owner-FCF components and core-column duplicates. Listed explicitly so a
# later vocabulary edit cannot quietly promote one spelling of a blocked
# concept while another stays blocked.
DCF_COMPONENT_KPIS: frozenset[str] = frozenset({
    "InterestIncome", "InterestExpense", "ShareRepurchases", "CashTaxesPaid",
    "EquityAwardTaxes", "DividendsPaid", "DeferredRevenue", "Depreciation",
    "LeaseLiabilities", "LeasePrincipalRepayment", "ProfitBeforeTax",
    "TaxExpense", "FinanceCosts", "NetDebt", "MarketableSecurities",
    "StockBasedComp", "DilutedShares", "StayInBusinessCapex", "GrowthCapex",
})

# Spelling -> canonical KPI name, for names written more than one way across
# the corpus. Same normalisation grammar as ALIASES above -- one grammar, not
# two that drift apart.
KPI_ALIASES: dict[str, str] = {
    # Depreciation, observed six ways
    "depreciation": "Depreciation",
    "danda": "Depreciation",
    "depreciationamortisation": "Depreciation",
    "depreciationandamortisation": "Depreciation",
    "depreciationamortization": "Depreciation",
    "depreciationandamortization": "Depreciation",
    "ebitdada": "Depreciation",
    # Snake-case variants emitted by build_facts_xbrl.py
    "cashtaxes": "CashTaxesPaid",
    "cashtaxespaid": "CashTaxesPaid",
    "interestincome": "InterestIncome",
    "interestexpense": "InterestExpense",
    "equityawardtaxes": "EquityAwardTaxes",
    "sharerepurchases": "ShareRepurchases",
    "buybacks": "ShareRepurchases",
    "deferredrevenue": "DeferredRevenue",
    "dividendspaid": "DividendsPaid",
    "dividendspaidcash": "DividendsPaid",
    "leaseliabilities": "LeaseLiabilities",
    "stockbasedcomp": "StockBasedComp",
    # IFRS-16 lease cash cost, observed under distinct literal spellings
    "leaserepayments": "LeasePrincipalRepaid",
    "interestexpenseleases": "LeaseInterestPaid",
    # Business KPIs
    "activecustomers": "ActiveCustomers",
    "customers": "Customers",
    "subscribers": "Subscribers",
    "mau": "MAU",
    "dau": "DAU",
    "dauq": "DAU",
    "dailyactiveusers": "DAU",
    "dailyactiveuniques": "DAU",
    "monthlyactiveusers": "MAU",
    "arpu": "ARPU",
    "averagerevenueperuser": "ARPU",
    "averagerevenueperunique": "ARPU",
    "adrevenue": "AdRevenue",
    "advertisingrevenue": "AdRevenue",
    "broadbandconnections": "BroadbandConnections",
    "gmv": "GMV",
    "grossmerchandisevolume": "GMV",
    "grossmerchandisevalue": "GMV",
    "tpv": "TPV",
    "totalpaymentvolume": "TPV",
    "totalorders": "TotalOrders",
    "orders": "TotalOrders",
    "processedvolume": "ProcessedVolume",
    "grossbookings": "GrossBookings",
    "aov": "AOV",
    "averageordervalue": "AOV",
    "cac": "CAC",
    "customeracquisitioncost": "CAC",
    "marketingroi": "MarketingROI",
    "takerate": "TakeRate",
    "revenueperactivecustomer": "RevenuePerActiveCustomer",
    "repeatorderspcttotal": "RepeatOrdersPctTotal",
    "marketingexpense": "MarketingExpense",
    "marketingspend": "MarketingExpense",
    "marketingpctrevenue": "MarketingPctRevenue",
    "arr": "ARR",
    "annualrecurringrevenue": "ARR",
    "digitalmediaarr": "DigitalMediaARR",
    "licensingrevenue": "LicensingRevenue",
    "royaltyrevenue": "RoyaltyRevenue",
    "researchanddevelopment": "ResearchAndDevelopment",
    "othergainsnet": "OtherGainsNet",
    "adjustednetprofit": "AdjustedNetProfit",
    "filmrightsamortisation": "FilmRightsAmortisation",
    "filmrightsamortization": "FilmRightsAmortisation",
    "totaladobearr": "TotalAdobeARR",
    "subscriptionrevenue": "SubscriptionRevenue",
    "netrevenueretention": "NetRevenueRetention",
    "dollarbasednetretentionrate": "NetRevenueRetention",
    "churnrate": "ChurnRate",
    "affo": "AFFO",
    "affopershare": "AFFOPerShare",
    "navpershare": "NAVPerShare",
    "ntapershare": "NTAPerShare",
    "occupancy": "Occupancy",
    "revpar": "RevPAR",
    "walt": "WALT",
    "aum": "AUM",
    "totalaum": "TotalAUM",
    "netinterestmargin": "NetInterestMargin",
    "grossrentalincome": "GrossRentalIncome",
    "managementfeeincome": "ManagementFeeIncome",
    "cashprofit": "CashProfit",
    "creditimpairmentcharge": "CreditImpairmentCharge",
    "customerdeposits": "CustomerDeposits",
    "netloansandadvances": "NetLoansAndAdvances",
    "operatingexpenses": "OperatingExpenses",
    "totalcommonequity": "TotalCommonEquity",
    "efficiencyratio": "EfficiencyRatio",
    "prepclprofit": "PrePCLProfit",
    "healthcarerevenue": "HealthcareRevenue",
    "industrialrevenue": "IndustrialRevenue",
    "healthcareebit": "HealthcareEBIT",
    "industrialebit": "IndustrialEBIT",
    "healthcareebitmargin": "HealthcareEBITMargin",
    "industrialebitmargin": "IndustrialEBITMargin",
    "healthcareorganicgrowth": "HealthcareOrganicGrowth",
    "industrialorganicgrowth": "IndustrialOrganicGrowth",
    "organicrevenuegrowth": "OrganicRevenueGrowth",
    "adjustedebitmargin": "AdjustedEBITMargin",
    "adjustedeps": "AdjustedEPS",
    "grossloansandadvances": "GrossLoansAndAdvances",
    "totaldeposits": "TotalDeposits",
    "casheps": "CashEPS",
    "timedeposits": "TimeDeposits",
    "fvtplfinancialassets": "FVTPLFinancialAssets",
    "fvtplinvestments": "FVTPLInvestments",
    "leaseprincipalrepaid": "LeasePrincipalRepaid",
    "cashgeneratedfromoperations": "CashGeneratedFromOperations",
    "leaseinterestpaid": "LeaseInterestPaid",
    "capitaliseddevelopment": "CapitalisedDevelopment",
    "intangibleassetpurchases": "IntangibleAssetPurchases",
    "netincomeincludingnci": "NetIncomeIncludingNCI",
    "realizedoilprice": "RealizedOilPrice",
    "realizedgasprice": "RealizedGasPrice",
    "salesvolume": "SalesVolume",
    "telecomservicesrevenue": "TelecomServicesRevenue",
    "evequity": "EVEquity",
    "interestrevenueonfinancialassets": "InterestRevenueOnFinancialAssets",
    "effectivecashtaxrate": "EffectiveCashTaxRate",
    "onlinemusicrevenue": "OnlineMusicRevenue",
    "socialentertainmentrevenue": "SocialEntertainmentRevenue",
    "bankborrowings": "BankBorrowings",
    "loansfromrelatedcompanies": "LoansFromRelatedCompanies",
    "shareplacingproceeds": "SharePlacingProceeds",
    "sharesissuedperiodend": "SharesIssuedPeriodEnd",
}


def normalize_kpi(name: object) -> str:
    """Canonical spelling of a `kpis.name`.

    Case- and punctuation-insensitive, mirroring `normalize()`. An unknown
    name normalises to itself (stripped of punctuation, original casing
    preserved) so the caller can still report it -- see `make kpi-coverage`.
    """
    key = "".join(ch for ch in str(name).lower() if ch.isalnum())
    return KPI_ALIASES.get(key, str(name))


def promote_header(name: object) -> str | None:
    """CSV header for a promotable business KPI, else None.

    None covers three cases that must all stay out of the CSV: an owner-FCF
    component, a name not in the whitelist, and any name whose header would
    collide with a core column.
    """
    canon = normalize_kpi(name)
    if canon in DCF_COMPONENT_KPIS:
        return None
    header = PROMOTE_KPIS.get(canon)
    if header is None or header in CSV_HEADERS:
        return None
    return header

def create_sql() -> str:
    """DDL for a ticker DB. Identical for every ticker -- that is the point."""
    cols = ",\n  ".join(f"{n} {t}" for n, t, _ in CORE_COLUMNS)
    derived = ",\n  ".join(f"{n} {t}" for n, t in DERIVED_COLUMNS)
    return f"""
CREATE TABLE IF NOT EXISTS core_metrics (
  {cols},
  {derived},
  PRIMARY KEY (period)
);

-- Cross-ticker comparison needs one scale. Tickers arrive with several:
-- SEC XBRL gives absolute dollars, NZX filings are usually thousands, and
-- some existing CSVs are already in millions. Recording `units` is not
-- enough on its own -- something has to normalise, or a query ranking by
-- revenue compares 22 against 45,183,036,000.
--
-- This view is the comparable surface. Query it, not core_metrics, when
-- ranking across tickers. Per-share figures (eps, dividend_per_share) and
-- percentages are never scaled.
--
-- NOTE: the view references core_metrics unqualified, so ATTACHing several
-- ticker DBs at once makes it ambiguous and DuckDB resolves it against the
-- wrong catalog. For cross-ticker work, open each DB separately and union
-- the results client-side (see scripts/screen_metrics.py) rather than
-- ATTACHing them together.
CREATE OR REPLACE VIEW metrics_normalized AS
WITH scaled AS (
  SELECT *,
    CASE lower(units)
      WHEN 'absolute'         THEN 1e-6
      WHEN 'absolute dollars' THEN 1e-6
      WHEN 'units'            THEN 1e-6
      WHEN 'thousands'        THEN 1e-3
      WHEN 'millions'         THEN 1.0
      WHEN 'billions'         THEN 1e3
      -- Unrecorded units are NULL, not assumed. Defaulting to millions
      -- silently produced 1000x errors: SEK.NZ files in thousands and
      -- read as NZ$411bn of revenue for a company that makes ~NZ$400m.
      -- A missing row is obvious; a plausible wrong one is not.
      ELSE NULL
    END AS k
  FROM core_metrics
)
SELECT
  period, currency, units AS units_raw,
  period_type, fiscal_year, months,
  -- money columns, all in millions of the reporting currency
  revenue * k              AS revenue,
  cost_of_revenue * k      AS cost_of_revenue,
  gross_profit * k         AS gross_profit,
  operating_income * k     AS operating_income,
  ebitda * k               AS ebitda,
  net_income * k           AS net_income,
  operating_cash_flow * k  AS operating_cash_flow,
  capex * k                AS capex,
  -- The stored sign of capex varies by ticker (positive outflow on some,
  -- cash-flow-statement negative on others); capex_abs is the outflow
  -- magnitude so a consumer never has to guess the convention.
  abs(capex) * k           AS capex_abs,
  free_cash_flow * k       AS free_cash_flow,
  shareholders_equity * k  AS shareholders_equity,
  total_assets * k         AS total_assets,
  total_liabilities * k    AS total_liabilities,
  total_debt * k           AS total_debt,
  cash_and_equivalents * k AS cash_and_equivalents,
  stock_based_comp * k     AS stock_based_comp,
  -- share counts scale with the same factor
  shares_outstanding * k   AS shares_outstanding,
  -- per-share and percentage figures are scale-free
  eps, dividend_per_share,
  gross_margin, operating_margin, net_margin
FROM scaled;

-- Company-specific metrics that cannot be a fixed column set.
CREATE TABLE IF NOT EXISTS kpis (
  period TEXT,
  name   TEXT,
  value  DOUBLE,
  unit   TEXT
);

-- Every hand correction to core_metrics or kpis, with what it replaced and
-- why. The DB is gitignored, so scripts/fix_metric.py mirrors each row into
-- Reports/{{T}}_Corrections.jsonl and load_existing.py replays that file
-- when it rebuilds a DB from a legacy CSV.
CREATE TABLE IF NOT EXISTS corrections (
  ts        TEXT,
  target    TEXT,     -- 'core_metrics' or 'kpis:<Name>'
  period    TEXT,
  col       TEXT,
  old_value DOUBLE,
  new_value DOUBLE,
  unit      TEXT,     -- for kpi rows: the unit written with the value
  source    TEXT,     -- filing file:line, or a stated basis
  actor     TEXT,
  op        TEXT      -- set / scale / derive / null / move / kpi / kpi_unit
);

-- What export_csv.py last wrote, so it can tell a CSV that was edited by
-- hand since (refuse: the edit belongs in the DB) from a DB that moved on
-- legitimately (export). DCBO's FY2022 row was corrected in the CSV, then
-- silently clobbered by the next export because nothing could tell.
CREATE TABLE IF NOT EXISTS export_log (
  ts      TEXT,
  sha256  TEXT,
  periods INTEGER
);

-- Raw extraction candidates. The agent adjudicates these; nothing here is
-- authoritative until it lands in core_metrics.
CREATE TABLE IF NOT EXISTS facts (
  metric      TEXT,
  period      TEXT,
  value_raw   DOUBLE,
  units_hint  TEXT,
  source_file TEXT,
  line_no     INTEGER,
  context     TEXT,
  confidence  TEXT,
  currency    TEXT
);
"""


def ensure_schema(con: "DuckDBPyConnection") -> None:
    """Create the ticker schema, and migrate a DB built by an older version.

    `create_sql()` alone is not enough on an existing DB: its
    `CREATE TABLE IF NOT EXISTS` is a no-op, so a table created before a
    core column was added keeps the old shape and every later INSERT dies
    with `Binder Error: Table "core_metrics" does not have a column with
    name "..."`. That is not a loud failure -- extract.py caught it, logged
    "XBRL yielded nothing", and silently fell back to text extraction for a
    US filer whose XBRL was fine (ADBE: a 24-column DB against a 28-column
    schema).

    Adding the missing columns is safe in both directions: DuckDB's
    ADD COLUMN fills existing rows with NULL, which is what an unpopulated
    metric means anyway, and the function is idempotent.
    """
    # Widen an existing table BEFORE running the DDL: metrics_normalized
    # selects every core column, so it cannot be created until they exist.
    have = {
        r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'core_metrics'"
        ).fetchall()
    }
    if have:
        wanted = [(n, t) for n, t, _ in CORE_COLUMNS] + DERIVED_COLUMNS
        for name, sqltype in wanted:
            if name not in have:
                con.execute(f"ALTER TABLE core_metrics ADD COLUMN {name} {sqltype}")
    con.execute(create_sql())


def backfill_period_columns(con: "DuckDBPyConnection") -> int:
    """Fill period_type / fiscal_year / months where they are still NULL.

    Delegates to periods.parse -- the single period grammar -- so the
    stored columns can never disagree with what the screens compute.
    Returns the number of rows written; idempotent, because an unparseable
    label is stored as 'OTHER' rather than left NULL to be retried.
    """
    import periods

    todo = con.execute(
        "SELECT period FROM core_metrics WHERE period_type IS NULL").fetchall()
    for (label,) in todo:
        p = periods.parse(label)
        con.execute(
            "UPDATE core_metrics SET period_type = ?, fiscal_year = ?, months = ?"
            " WHERE period = ?",
            [p.ptype, p.fiscal_year, p.months, label])
    return len(todo)


if __name__ == "__main__":
    print(create_sql())
    print(f"-- {len(CORE_COLUMNS)} core columns, {len(ALIASES)} aliases mapped")
