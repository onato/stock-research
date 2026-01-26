# Block, Inc. (XYZ) Financial Metrics Extraction

## Overview
This directory contains scripts to extract financial metrics from Block's SEC filings (10-K annual and 10-Q quarterly reports) and output to CSV format for analysis and DCF modeling.

## Files Created

1. **extract_metrics.py** - Main extraction script (Python 3)
2. **run_extraction.sh** - Shell script to execute the extraction
3. **Reports/XYZ_Metrics.csv** - Output CSV file (generated after running script)

## How to Run

### Option 1: Direct Python Execution
```bash
cd /Users/swilliams/Stocks/Research/XYZ
python3 extract_metrics.py
```

### Option 2: Using Shell Script
```bash
cd /Users/swilliams/Stocks/Research/XYZ
chmod +x run_extraction.sh
./run_extraction.sh
```

## Output Format

The script generates `/Users/swilliams/Stocks/Research/XYZ/Reports/XYZ_Metrics.csv` with the following columns:

### Core Financial Metrics
- **Period**: Quarter (Q1 2024) or Fiscal Year (FY2024)
- **Revenue**: Total net revenue in millions USD
- **GrossProfit**: Revenue minus cost of revenue in millions USD
- **GrossMargin**: Gross profit as % of revenue
- **OperatingIncome**: Operating income/loss in millions USD (negative for losses)
- **OperatingMargin**: Operating income as % of revenue
- **NetIncome**: Net income/loss in millions USD (negative for losses)
- **NetMargin**: Net income as % of revenue
- **EPS**: Diluted earnings per share in USD

### Cash Flow Metrics
- **OperatingCashFlow**: Cash from operating activities in millions USD
- **CapitalExpenditures**: CapEx in millions USD (positive number)
- **FreeCashFlow**: Operating cash flow minus CapEx in millions USD

### Balance Sheet Metrics (for DCF)
- **ShareholdersEquity**: Total stockholders' equity in millions USD
- **TotalDebt**: Short-term + long-term debt in millions USD
- **CashAndEquivalents**: Cash + short-term investments in millions USD
- **SharesOutstanding**: Diluted shares outstanding in millions

### Block-Specific KPIs
- **TransactionRevenue**: Transaction-based revenue in millions USD
- **SubscriptionRevenue**: Subscription and services revenue in millions USD
- **HardwareRevenue**: Hardware revenue in millions USD
- **BitcoinRevenue**: Bitcoin revenue in millions USD
- **SquareGPV**: Square Gross Payment Volume in millions USD
- **CashAppInflows**: Cash App inflows in millions USD
- **CashAppMonthlyActives**: Cash App monthly active users in millions
- **SquareMonthlyActives**: Square monthly active sellers/merchants in millions

## Data Sources

The script parses 40 text files in the `Extracted/` directory:

- **10 Annual Reports (10-K)**: FY2015 through FY2024
- **30 Quarterly Reports (10-Q)**: Q1-2016 through Q3-2025

## Extraction Methodology

1. **File Reading**: Loads extracted text from PDF conversions (single-line format from pdftotext)
2. **Section Identification**: Uses regex to find:
   - Consolidated Statements of Operations
   - Consolidated Balance Sheets
   - Consolidated Statements of Cash Flows
3. **Data Parsing**: Extracts numeric values from financial tables
4. **Unit Conversion**: Converts from thousands (SEC standard) to millions
5. **Calculated Metrics**: Computes margins and free cash flow
6. **KPI Extraction**: Searches MD&A sections for business metrics
7. **Chronological Sorting**: Orders periods from oldest to newest
8. **CSV Generation**: Writes structured data with proper headers

## Key Parsing Challenges

- **Single-line format**: PDF extraction creates files with all content on 1-2 very long lines
- **Inconsistent labeling**: Earlier years use different terminology (e.g., "Operating loss" vs "Operating income")
- **Unit variations**: Some filings in thousands, others in millions
- **Missing KPIs**: GPV and monthly actives not reported in early years
- **Bitcoin volatility**: Bitcoin revenue/profit highly variable quarter-to-quarter

## Data Quality Notes

- **Early years (2015-2017)**: Limited KPI disclosures; focus on core financials
- **2018+**: Expanded segment reporting with Square vs Cash App breakdown
- **2020+**: Added monthly active users and GPV metrics consistently
- **2022+**: Afterpay acquisition impacts revenue mix
- **2025**: Ticker changed from SQ to XYZ (January 2025)

## Verification

After running the script, verify the output:

1. Check total periods: Should have ~40 rows (10 annual + 30 quarterly)
2. Spot-check recent quarters against earnings releases
3. Verify revenue trends are logical (generally increasing)
4. Confirm no major gaps in critical fields (Revenue, Net Income, EPS)
5. Check that margins are reasonable (Gross Margin 40-60%, Operating Margin -20% to +20%)

## Expected Results

Based on the analysis file and recent earnings:

**Most Recent Quarters (Q1-Q3 2025):**
- Q3 2025: Gross Profit ~$2,660M, Net Income positive, EPS ~$1.20-1.50
- Q2 2025: Gross Profit ~$2,500M
- Q1 2025: Gross Profit ~$2,400M

**Full Year 2024 (FY2024):**
- Gross Profit: $8,890M
- Net Income: Positive (turned profitable)
- Free Cash Flow: $2,070M

**Full Year 2023 (FY2023):**
- Gross Profit: ~$7,500M
- Free Cash Flow: $515M

## Troubleshooting

If the script fails:

1. **Verify Python 3 is installed**: `python3 --version`
2. **Check file permissions**: Ensure script is readable and output directory is writable
3. **Verify input files exist**: Check that all 40 .txt files are in `Extracted/` directory
4. **Review error messages**: Script prints status for each file parsed
5. **Test on single file**: Modify script to process only one file for debugging

## Next Steps

After generating XYZ_Metrics.csv:

1. **Create Dashboard**: Use the CSV to build an HTML dashboard (see WISE.L and DUOL examples)
2. **DCF Model**: Use ShareholdersEquity, TotalDebt, CashAndEquivalents, and SharesOutstanding for valuation
3. **Trend Analysis**: Analyze revenue growth, margin expansion, and FCF generation
4. **KPI Monitoring**: Track Square GPV growth vs Cash App growth rates
5. **Competitive Comparison**: Compare metrics to PayPal, Stripe, Toast

## Author Notes

This extraction script is designed to handle Block's unique reporting structure:
- Dual ecosystem (Square + Cash App) reporting
- Heavy Bitcoin revenue exposure (51% of Cash App)
- Transition from growth-at-all-costs to profitability (2023-2024)
- Acquisitions (Afterpay $29B in 2022)
- Ticker change (SQ → XYZ in January 2025)

The script prioritizes:
1. Accuracy of core financial metrics for DCF modeling
2. Capture of Block-specific KPIs (GPV, monthly actives)
3. Handling of losses in early years (negative operating/net income)
4. Consistent units (millions USD) across all periods
