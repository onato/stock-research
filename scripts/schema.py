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
CSV_HEADERS: list[str] = [c[2] for c in CORE_COLUMNS]

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
    # Recurring revenue
    "ARR": "ARR",
    "SubscriptionRevenue": "SubscriptionRevenue",
    "NetRevenueRetention": "NetRevenueRetention",
    "ChurnRate": "ChurnRate",
    # Property / financial sector staples
    "AFFO": "AFFO",
    "AFFOPerShare": "AFFOPerShare",
    "NAVPerShare": "NAVPerShare",
    "NTAPerShare": "NTAPerShare",
    "Occupancy": "Occupancy",
    "WALT": "WALT",
    "AUM": "AUM",
    "NetInterestMargin": "NetInterestMargin",
    # Net-revenue reporters (payments, marketplaces) have no gross Revenue
    # line -- NetRevenue is the top line, so it must reach the CSV.
    "NetRevenue": "NetRevenue",
    "PayablesToMerchants": "PayablesToMerchants",
    # Disclosed operating detail dashboards chart directly
    "EBITDAMargin": "EBITDAMargin",
    "EPS_Basic": "EPS_Basic",
    "FTE": "FTE",
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
    "netinterestmargin": "NetInterestMargin",
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
    return f"""
CREATE TABLE IF NOT EXISTS core_metrics (
  {cols},
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
  -- money columns, all in millions of the reporting currency
  revenue * k              AS revenue,
  cost_of_revenue * k      AS cost_of_revenue,
  gross_profit * k         AS gross_profit,
  operating_income * k     AS operating_income,
  ebitda * k               AS ebitda,
  net_income * k           AS net_income,
  operating_cash_flow * k  AS operating_cash_flow,
  capex * k                AS capex,
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


if __name__ == "__main__":
    print(create_sql())
    print(f"-- {len(CORE_COLUMNS)} core columns, {len(ALIASES)} aliases mapped")
