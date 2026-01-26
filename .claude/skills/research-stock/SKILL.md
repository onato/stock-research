---
name: research-stock
description: Downloads financial reports from company IR website, extracts data, and creates a CSV-backed HTML dashboard for stock research
allowed-tools: Bash, Read, Write, Edit, WebFetch, WebSearch, Glob, Grep, Task
argument-hint: [TICKER]
---

# Stock Research Workflow

You are researching the stock: $ARGUMENTS

Create the directory structure first:
```bash
mkdir -p "./$ARGUMENTS/PDFs" "./$ARGUMENTS/Extracted" "./$ARGUMENTS/Reports"
```

## Step 0: Check for Existing Data

**Before downloading anything, check what data already exists:**

```bash
# List existing PDFs
ls -la "./$ARGUMENTS/PDFs/" 2>/dev/null || echo "No PDFs folder yet"

# List existing extracted text
ls -la "./$ARGUMENTS/Extracted/" 2>/dev/null || echo "No Extracted folder yet"

# List existing reports
ls -la "./$ARGUMENTS/Reports/" 2>/dev/null || echo "No Reports folder yet"
```

**Skip downloading if reports already exist:**
- If `./$ARGUMENTS/PDFs/` contains PDF files, **DO NOT re-download** them
- If `./$ARGUMENTS/Extracted/` contains .txt files, **DO NOT re-extract** them
- Only download reports that are missing (e.g., newer quarters/years)

**When to re-download:**
- No PDFs exist yet
- User explicitly asks to refresh/update the data
- A new quarter/year has been released since last download

## Step 1: Find Investor Relations Website

**Skip this step if PDFs already exist in `./$ARGUMENTS/PDFs/`**

Search for "{ticker} investor relations" to find the company's IR page.
Common patterns:
- investor.{company}.com
- {company}.com/investors
- ir.{company}.com
- For international tickers (e.g., SEK.NZ, WISE.L), search for the company name

## Step 2: Download Financial Reports

**If PDFs already exist:** Check the IR website for any NEW reports (quarters/years) published since last download. Only download missing reports.

**If no PDFs exist:** Download full history.

**IMPORTANT: Download as much historical data as available. Target 10 years for both annual AND quarterly data.**

Navigate to financial reports section and download:
- **Annual Reports / 10-K / 20-F**: All available, up to 10 years
- **Quarterly Reports / 10-Q / 6-K**: All available, up to 10 years (40 quarters) - download as many as exist
- Earnings presentations (optional, for additional context)

Note: Quarterly data should cover the same time period as annual data so charts are consistent.

For US companies: Look for SEC filings (10-K, 10-Q)
For international companies: Look for 20-F (annual) and 6-K (quarterly)
For non-US listed: Look for Annual Reports and Interim/Half-Year Reports

Save PDFs to: ./$ARGUMENTS/PDFs/

Download using curl:
```bash
curl -L -o "./$ARGUMENTS/PDFs/{filename}" "{url}" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
```

## Step 3: Rename Files

**Skip if files are already renamed (check if filenames match {TICKER}_{type}_{period}.pdf pattern)**

Rename to standardized format: {TICKER}_{report_type}_{period}.pdf

Examples:
- AAPL_10K_FY2024.pdf
- AAPL_10Q_Q1-2025.pdf
- BABA_20F_FY2024.pdf
- BABA_6K_Q3-2024.pdf
- SEK.NZ_Annual_FY2024.pdf
- SEK.NZ_HalfYear_H1-2024.pdf

## Step 4: Extract Text

**Skip if .txt files already exist in `./$ARGUMENTS/Extracted/` for each PDF**

Only extract text for PDFs that don't have a corresponding .txt file yet.

Run pdftotext to extract readable text:
```bash
pdftotext -layout "./$ARGUMENTS/PDFs/{input}.pdf" "./$ARGUMENTS/Extracted/{output}.txt"
```

Store extracted text in: ./$ARGUMENTS/Extracted/

## Step 5: Parse Financial Metrics

**Always regenerate** - may include data from newly downloaded reports.

Read the extracted text and identify key financial metrics.

**IMPORTANT: Include BOTH annual AND quarterly data in the CSV. Each row is one period.**

Standard metrics to look for:
- Period (e.g., "Q1 2024", "Q2 2024", "FY2023" - use consistent format)
- Revenue / Net Sales
- Gross Profit & Margin
- Operating Income / EBIT / EBITDA
- Net Income
- EPS (Basic & Diluted)
- Free Cash Flow

Also identify company-specific KPIs based on the business model (see Step 6).

Write metrics to CSV: ./$ARGUMENTS/Reports/{TICKER}_Metrics.csv

CSV Format (note: includes both quarterly AND annual data, sorted chronologically):
```csv
Period,Revenue,GrossProfit,GrossMargin,OperatingIncome,NetIncome,EPS,FreeCashFlow,...
Q1 2020,300.0,140.0,46.7,55.0,30.0,0.30,25.0,...
Q2 2020,310.0,145.0,46.8,58.0,32.0,0.32,27.0,...
Q3 2020,320.0,150.0,46.9,60.0,34.0,0.34,28.0,...
Q4 2020,330.0,155.0,47.0,62.0,36.0,0.36,30.0,...
FY2020,1260.0,590.0,46.8,235.0,132.0,1.32,110.0,...
Q1 2021,...
```

The CSV should contain:
- All quarterly periods from the 10-Q/6-K reports (2-3 years of quarters)
- All annual periods from the 10-K/20-F reports (up to 10 years)
- Sorted in chronological order (oldest first)

## Step 6: Qualitative Analysis

**Always regenerate** - includes recent developments and current market context that should be refreshed each run.

Perform qualitative analysis to understand the business beyond the numbers. Use web search and the extracted reports.

Use the qualitative-analyst agent instructions in `.claude/agents/qualitative-analyst.md` to gather:
- Company overview (2-3 sentence description)
- Business model and revenue streams
- Competitive position and moat factors
- Key risks (business, financial, regulatory, macro)
- Growth drivers
- Recent developments (last 6-12 months)
- Bull case and bear case

Write the analysis to: ./$ARGUMENTS/Reports/{TICKER}_Analysis.json

This analysis will be embedded in the dashboard.

## Step 7: Generate Dashboard

**IMPORTANT: Do NOT reference other ticker dashboards (WISE, DUOL, etc.). Create a fully self-contained dashboard.**

Before generating the dashboard, analyze the extracted reports to understand:
1. **Business Model**: How does this company make money?
2. **Key Metrics**: What does management emphasize in earnings calls/letters?
3. **Industry Context**: What metrics matter for this type of business?

Use the dashboard-generator agent instructions in `.claude/agents/dashboard-generator.md` for:
- Complete CSS styling (dark theme, glassmorphism cards)
- JavaScript patterns (Chart.js, embedded CSV parsing)
- Help modal structure

Dashboard must include:
1. **Embed CSV data directly in the HTML** - Do NOT use fetch() to load external files (won't work with file:// URLs)
2. Self-contained HTML with embedded CSS/JS (only Chart.js CDN is external)
3. KPI cards with current values and YoY changes
4. Chart.js visualizations tailored to the business model
5. Help buttons (?) with company-specific metric explanations
6. Derived metrics (growth rates, margins, ratios relevant to THIS company)

Output: ./$ARGUMENTS/Reports/{TICKER}_Dashboard.html

## Step 8: DCF Valuation

**Always regenerate** - DCF contains current price and valuation data that should be refreshed each run.

Create a DCF valuation model based on the financial data and qualitative analysis.

Use the dcf-analyst agent instructions in `.claude/agents/dcf-analyst.md` to:

1. **Calculate Historical Growth Rates**
   - 3-year and 5-year CAGRs for Revenue, EPS, FCF, and Equity
   - Select equity growth as primary driver (most conservative)
   - Flag if equity CAGR diverges >30% from revenue/EPS

2. **Create Scenario Projections**
   - **Base Case**: Historical equity CAGR, decelerating toward terminal growth
   - **Bull Case**: +5-10pp above base, aligned with bull thesis
   - **Bear Case**: -5-10pp below base, risk factors materialize

3. **DCF Calculations**
   - Project FCF for 5 years
   - Calculate Terminal Value using Gordon Growth model
   - Discount to present value using WACC
   - Subtract net debt, divide by shares for intrinsic value

4. **Entry Price Calculation**
   - Calculate price to achieve 15% CAGR to terminal value
   - `Entry Price = Terminal Value per Share / (1.15)^5`

5. **Sensitivity Analysis**
   - Matrix of IV across WACC (+/-2%) and Terminal Growth (+/-1%)

6. **Generate Outputs**
   - `./$ARGUMENTS/Reports/{TICKER}_DCF.json` - Embedded in dashboard
   - `./$ARGUMENTS/Reports/{TICKER}_DCF_Model.xlsx` - Downloadable Excel model

The dashboard generator will embed the DCF JSON and add an interactive valuation section with:
- Intrinsic Value and Entry Price cards
- Adjustable sliders for Growth Rate, WACC, Terminal Growth
- Real-time recalculation when inputs change
- Scenario tabs (Bull/Base/Bear)
- Sensitivity matrix
- Download Excel button

## Final Checklist

**Data Collection (cached - only download new reports):**
- [ ] Check for existing PDFs in ./$ARGUMENTS/PDFs/
- [ ] Download only NEW quarterly/annual reports not already present
- [ ] Rename any new files to standard format
- [ ] Extract text only for new PDFs (skip existing .txt files)

**Analysis (regenerate each run for fresh data):**
- [ ] Metrics CSV rebuilt from all extracted text (includes any new data)
- [ ] Metrics CSV includes DCF fields: ShareholdersEquity, TotalDebt, CashAndEquivalents, SharesOutstanding
- [ ] Qualitative analysis JSON refreshed with current market context and recent developments
- [ ] DCF valuation JSON refreshed with current stock price and updated projections
- [ ] DCF Excel model regenerated

**Dashboard (regenerate each run):**
- [ ] Dashboard embeds CSV data, analysis JSON, AND DCF JSON directly
- [ ] Dashboard has Investment Overview section at the top
- [ ] Dashboard has DCF Valuation section at the bottom
- [ ] DCF sliders update intrinsic value in real-time
- [ ] Sensitivity matrix displays correctly with color coding
- [ ] Growth divergence warning appears if applicable
- [ ] Dashboard is self-contained (no references to other tickers)
- [ ] Dashboard metrics are tailored to this company's business model
- [ ] Dashboard works when opened as local file (file:// URL)

**Note:** To force re-download of ALL reports (not just new ones), delete the PDFs folder:
```bash
rm -rf "./$ARGUMENTS/PDFs"
```
