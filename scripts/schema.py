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
    "BroadbandConnections": "BroadbandConnections",
    # Volume
    "GMV": "GMV",
    "TPV": "TPV",
    "TotalOrders": "TotalOrders",
    "ProcessedVolume": "ProcessedVolume",
    "GrossBookings": "GrossBookings",
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
    "AdjustedEBITMargin": "AdjustedEBITMargin",
    "AdjustedEPS": "AdjustedEPS",
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
    # Property advertising vs financial services revenue split (REA Group,
    # REA.AX): the core listings business, financial services (Mortgage
    # Choice) is a bolt-on segment whose revenue steps up sharply -- from
    # ~$100m to ~$325m in FY2022 on the Mortgage Choice acquisition -- so a
    # single Revenue column hides the margin-mix shift toward the lower-margin
    # broking business.
    "PropertyAdvertisingRevenue": "PropertyAdvertisingRevenue",
    "FinancialServicesRevenue": "FinancialServicesRevenue",
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
    # Property / financial sector staples
    "AFFO": "AFFO",
    "AISC": "AISC",                       # gold miners: all-in sustaining cost per oz
    "GoldProduction": "GoldProduction",
    "CopperProduction": "CopperProduction",
    "GoldPriceAchieved": "GoldPriceAchieved",
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
    "NAVPerShare": "NAVPerShare",
    "NTAPerShare": "NTAPerShare",
    "Occupancy": "Occupancy",
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
    # Business KPIs
    "activecustomers": "ActiveCustomers",
    "customers": "Customers",
    "subscribers": "Subscribers",
    "mau": "MAU",
    "dau": "DAU",
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
