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
CORE_COLUMNS = [
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
    ("units",                "TEXT",   "Units"),
    ("currency",             "TEXT",   "Currency"),
]

CORE_NAMES = [c[0] for c in CORE_COLUMNS]
CSV_HEADERS = [c[2] for c in CORE_COLUMNS]

# Existing CSV headers -> core column. Drawn from the aliases actually
# observed across the 67 committed CSVs, plus the legacy names called out
# in financial-parser.md (SBC / StockBasedComp / ShareBasedComp).
ALIASES = {
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
    "units": "units", "currency": "currency",
}


def normalize(header):
    """Map a CSV header to its core column, or None if it is a KPI.

    Case- and punctuation-insensitive: `Gross Margin`, `gross_margin` and
    `GrossMargin` all resolve to `gross_margin`.
    """
    key = "".join(ch for ch in str(header).lower() if ch.isalnum())
    return ALIASES.get(key)


def create_sql():
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
  confidence  TEXT
);
"""


if __name__ == "__main__":
    print(create_sql())
    print(f"-- {len(CORE_COLUMNS)} core columns, {len(ALIASES)} aliases mapped")
