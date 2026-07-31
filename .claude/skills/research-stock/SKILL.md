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

### Step 5a: Build the facts table first (fast, no model)

```bash
python3 .github/scripts/build_facts.py "$ARGUMENTS"
```

One linear pass over `Extracted/*.txt`, writing candidate values to
`./$ARGUMENTS/Reports/$ARGUMENTS.duckdb`. Takes ~1 second for 18 filings.

**This must run before the financial-parser agent is spawned.** The agent
queries the facts table instead of grepping the filings — that search
previously took 183 model turns and 18.2M cache-read tokens on a single
ticker, roughly 60% of its total cost.

### Step 5b: Adjudicate with the financial-parser agent

The agent (`.claude/agents/financial-parser.md`) resolves competing candidates,
determines units and currency, and writes **both** the `core_metrics` table and
the exported CSV. `core_metrics` uses a fixed schema shared by every ticker in
this repo (see `.github/scripts/schema.py`), which is what makes cross-ticker
screening possible.

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
5. Log-scale toggle ("Log" button) on absolute-value time-series charts (skip percentage metrics and series with zero/negative values — see dashboard-generator.md for the pattern)
6. Help buttons (?) with company-specific metric explanations
7. Derived metrics (growth rates, margins, ratios relevant to THIS company)

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

3. **Owner-FCF Adjustment** (always — build the DCF on owner FCF, not reported FCF)
   - `last_fcf = reported_fcf − SBC_incl_equity_taxes − interest_income`, applied across the whole history so growth rates use the adjusted series
   - **SBC must include cash taxes on equity awards** (financing-activity withholding), not just the cash-flow add-back
   - **Strip interest income whenever net cash > 1× the FCF base** — leaving it in while also adding net cash to EV counts the cash pile twice
   - **Project forward SBC as a % of revenue**, declining gradually (e.g. 15% → 10%). Never hold SBC flat in dollars while revenue compounds — that silently assumes the comp problem solves itself
   - Share count grows only by dilution buybacks fail to absorb: `uncovered = max(0, SBC − buyback_cash)`
   - Charges the cost exactly once: buyback-heavy names keep a flat share count, non-repurchasers dilute
   - If any input is missing from the metrics CSV, extract it from `Extracted/` and backfill — never skip the adjustment silently

4. **Build the FCF margin from components** — never carry the latest year's margin forward flat
   - `adj_EBITDA − SBC − D&A → EBIT → ×(1−cash_tax) → +D&A − capex + working capital = owner FCF`
   - **Working capital scales with the CHANGE in revenue** (capture ~15-20% of the YoY increase), not with revenue. Deferred-revenue tailwinds shrink hard when growth decelerates — DUOL's was 34% of FY2025 reported FCF
   - **Ramp the cash tax rate to normal** if the latest year had a valuation-allowance release or one-off credit (DUOL: −6% guided 2026 → ~21%)
   - Sanity-check the projected year-1 owner-FCF margin against the component build; if it is far above the latest actual, an unrepeatable one-off is being carried forward

5. **DCF Calculations**
   - Project owner FCF for 5 years from the component build
   - Terminal Value = **lower of** Gordon Growth and a 20× terminal-FCF cap (12-15× for cyclicals); record which bound
   - Discount to present value using WACC
   - Subtract net debt, divide by the **year-5 projected share count** for intrinsic value
   - Emit a memo line for value-per-share *if SBC were non-cash* — the spread shows how much the answer hangs on SBC treatment

6. **Entry Price Calculation**
   - Entry price = the price at which buying today, collecting 5 years of projected FCF, and exiting at fair value in year 5 earns a 15% IRR
   - `Entry Price = ( Σ FCF_t / 1.15^t  +  TV_5 / 1.15^5  −  net_debt ) / projected_shares[5]` (t = 1..5, using the scenario's projected SBC-adjusted FCF path; TV_5 is the Gordon terminal value computed with WACC)
   - Do NOT discount only the terminal value — that ignores the interim FCF the investor receives and the net cash on the balance sheet, and produces an entry price far too low
   - Sanity property: when projected growth ≈ 15%, entry price lands modestly below intrinsic value (because the 15% hurdle exceeds WACC) — never at ~half of it

7. **Sensitivity Analysis**
   - Matrix of IV across WACC (+/-2%) and Terminal Growth (+/-1%)

8. **Generate Outputs**
   - `./$ARGUMENTS/Reports/{TICKER}_DCF.json` - Embedded in dashboard
   - `./$ARGUMENTS/Reports/{TICKER}_DCF_Model.xlsx` - Downloadable Excel model

The dashboard generator will embed the DCF JSON and add an interactive valuation section with:
- Intrinsic Value and Entry Price cards
- Adjustable sliders for Growth Rate, WACC, Terminal Growth
- Real-time recalculation when inputs change
- Scenario tabs (Bull/Base/Bear)
- Sensitivity matrix
- Download Excel button

## Step 8b: Verify Stock Price in DCF

After DCF generation, verify the stock price is accurate:

1. Fetch the live price from Yahoo Finance:
```bash
curl -s "https://query1.finance.yahoo.com/v8/finance/chart/$ARGUMENTS?range=1d&interval=1d" \
  -H "User-Agent: Mozilla/5.0" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['chart']['result'][0]['meta']['regularMarketPrice'])"
```

2. Read `current_price` from `./$ARGUMENTS/Reports/{TICKER}_DCF.json`

3. If the prices differ by more than 5%, update the DCF JSON:
   - Set `current_price` to the Yahoo Finance price
   - Recalculate `upside` for all three scenarios: `((intrinsic_value / new_price) - 1) * 100`
   - Recalculate `probability_weighted.weighted_iv` is unchanged (it's based on intrinsic values, not current price) — but verify the `entry_price.base.entry_discount_from_current` is updated: `((current_price - entry_price) / current_price) * -100`. The `entry_price` value itself does not depend on current price, so it never needs recomputing on price drift.
   - Write the updated JSON back to the file

4. If the prices match within 5%, no changes needed — log that the price was verified.

5. **If the DCF JSON was updated**, re-embed the updated DCF JSON into the dashboard HTML (`./$ARGUMENTS/Reports/{TICKER}_Dashboard.html`) by replacing the existing `const dcfData = {...};` block with the corrected data. This ensures the dashboard displays the verified price.

## Step 8c: DCF Sanity Check (Implied Multiples vs History)

**Always run after Step 8b.** This step exists because DCF models can produce intrinsic values implying multiples the market has never paid. SEK.NZ (Seeka) was the canonical failure: a peak-FCF DCF produced IV $22.75 implying 30x P/E and 3.4x P/B, despite Seeka never trading above 0.99x P/B or 7.8x EV/EBITDA in 10 years.

### Step 8c.1: Compute historical multiples from local data

Fetch 10 years of monthly closing prices from Yahoo Finance and align FY-end prices with FY metrics in `{TICKER}_Metrics.csv`:

```bash
curl -s "https://query1.finance.yahoo.com/v8/finance/chart/$ARGUMENTS?range=10y&interval=1mo" \
  -H "User-Agent: Mozilla/5.0" | python3 -c "
import sys, json
from datetime import datetime
d = json.load(sys.stdin)
r = d['chart']['result'][0]
for t, c in zip(r['timestamp'], r['indicators']['quote'][0]['close']):
    if c: print(datetime.fromtimestamp(t).strftime('%Y-%m'), round(c, 2))
"
```

For each FY row in the metrics CSV, match the FY-end month's closing price (use Dec for calendar-year FYs, Jun for June FYs, etc. — check the company's reporting calendar). For each year compute:

- **P/E** = price / (EPS − SBC_per_share) (skip years where adjusted EPS is negative)
- **P/B** = (price × shares_outstanding) / total_equity
- **EV/EBITDA** = (market_cap + net_debt) / (EBITDA − SBC)
- **P/Sales** = market_cap / revenue

**Earnings-based multiples are computed on SBC-adjusted earnings on both sides** — historical and implied — so the comparison is apples-to-apples with the SBC-adjusted DCF. Where `StockBasedComp` is unavailable for a historical year, mark that year's adjusted multiple as unavailable rather than silently mixing adjusted and unadjusted figures.

These deliberately will **not** match published P/E figures from Yahoo or stock screeners, which use GAAP EPS. Note this in the JSON so a future reader doesn't "correct" it back.

Exclude any clear outlier years (cyclone, COVID write-down, one-off impairment) from the averages — but keep them in the table for visibility.

### Step 8c.2: Compute implied multiples from base case IV

From the base case `intrinsic_value` in the DCF JSON, compute what multiples that IV would imply on the most recent FY actuals:

- **Implied P/E** = base_IV / (latest_EPS − latest_SBC_per_share)
- **Implied P/B** = base_IV / (latest_equity / shares_outstanding)
- **Implied EV/EBITDA** = (base_IV × shares + net_debt) / (latest_EBITDA − latest_SBC)

Use the same SBC-adjusted basis as Step 8c.1 — comparing an SBC-adjusted implied multiple against an unadjusted historical average would manufacture a false trip.

### Step 8c.3: Trip detection

Sanity check **fails** if ANY of:
- Implied P/E > 2.0× the 10yr average (excluding outlier years)
- Implied P/B > 1.5× the 10yr maximum
- Implied EV/EBITDA > 1.5× the 10yr maximum
- Base case upside > 100% AND current price is in the upper half of the 10yr price range (suggests the market already incorporates the good news)

### Step 8c.4: Auto-diagnose root cause

If sanity check fails, identify which assumption is producing the over-valuation. Check in order:

1. **Peak-earnings extrapolation**: Is `last_fcf` near the highest value in the company's FCF history? Compute the 5-8 year FCF mean and median **from the SBC-adjusted series** (both sides adjusted — comparing an adjusted base against an unadjusted median would understate the peak). If `last_fcf` > 1.5× the historical median, the base year is a peak.
2. **WACC too low for the risk profile**: Are there structural risks not in the WACC? Look at the qualitative analysis for: single-customer concentration, small-cap illiquidity (market cap < $500m), extreme cyclicality (EBITDA range > 3× in 5 years), regulatory single-point-of-failure. Each unaccounted structural risk should add 1.5-2.5% to WACC.
3. **Growth rate too aggressive**: Are projected growth rates above the historical revenue/EBITDA CAGRs through-the-cycle (not the recent recovery CAGR)? Through-cycle growth should reflect industry volume growth, not cyclical bounce-back.
4. **Terminal growth too high**: Is `terminal_growth` near or above long-run GDP growth for a mature business? For cyclical/agricultural businesses it should be 1.5-2.5% maximum.

### Step 8c.5: Auto-apply the fix

Pick the most defensible fix and apply it without prompting. Apply in priority order:

1. **If peak-earnings extrapolation is the cause** (most common): rebuild with mid-cycle FCF, using **SBC-adjusted figures throughout**. Set base case `fcf_base_year` to the mid-point of (historical median adjusted FCF, latest adjusted FCF). Add `mid_cycle_fcf` field documenting the choice. Bull case can use latest adjusted FCF × 0.85. Bear case can use historical median × 0.75. The SBC deduction is never reversed by this step — mid-cycling and SBC adjustment compose.

2. **If structural WACC understatement is the cause**: add explicit premium components to the base WACC. Use this template (additive, on top of standard 11-12% NZ/AU small-cap WACC):
   - Single-customer concentration (>40% of revenue from one customer): +1.5-2.5%
   - Small-cap illiquidity (market cap < $300m): +1.5-2.5%
   - Extreme cyclicality (EBITDA range > 3× peak-to-trough): +1.0-2.0%
   - Regulatory single-point-of-failure: +1.0-2.0%
   - Concentrated geographic risk (one growing region, one country): +0.5-1.5%
   
   Document each premium added in `wacc_rationale`.

3. **If aggressive growth is the cause**: cap projected growth at the through-cycle revenue CAGR (compute from first-to-last year of available data, NOT the cyclical recovery window).

4. **Add exit-multiple cap**: regardless of root cause, add an `exit_multiple_cap` (in EV/EBITDA terms) per scenario equal to the historical-range multiple: bear case = 10yr min, base case = 10yr average, bull case = 10yr maximum. In the DCF calc, use `min(gordon_growth_tv, exit_multiple_cap × terminal_ebitda)`.

After the fix, recompute and verify the implied multiples are within sanity bounds. If not, iterate once more (e.g., further reduce mid-cycle FCF, raise WACC).

### Step 8c.6: Surface the change with a warning banner

In the dashboard, add a yellow warning banner above the DCF section explaining what was changed and why. Use this template:

```html
<div style="max-width:1400px;margin:0 auto 20px;padding:16px 20px;background:rgba(253,203,110,0.08);border:1px solid rgba(253,203,110,0.3);border-radius:10px;font-size:0.9rem;line-height:1.6;color:#d0d0d0">
<strong style="color:#fdcb6e">Model approach:</strong> [One-paragraph explanation of which sanity check tripped, what was changed, and the resulting implied multiples vs historical averages. Conclude with the investment framing — multi-bagger thesis vs income+modest-growth, etc.]
</div>
```

Also populate a `valuation_philosophy` block in the DCF JSON capturing the same explanation.

### Step 8c.7: Record the sanity check in the DCF JSON

Add a `sanity_check` block to the DCF JSON regardless of pass/fail:

```json
"sanity_check": {
  "ran": true,
  "passed": false,
  "implied_multiples": {"pe": 29.9, "pb": 3.36, "ev_ebitda": 11.5},
  "historical_averages_ex_outliers": {"pe_avg": 13.1, "pb_avg": 0.72, "pb_max": 0.99, "ev_ebitda_avg": 5.6, "ev_ebitda_max": 7.8},
  "trip_reasons": ["Implied P/B 3.36x exceeds 1.5x historical max 0.99x", "Base case upside 354% with price in upper half of 10yr range"],
  "diagnosis": "Peak-FCF extrapolation: base year FCF $79m is 2.4x historical median $32m",
  "fix_applied": "Switched to mid-cycle FCF $50m; added structural WACC premiums totalling +5.5% (Zespri concentration +2%, NZX illiquidity +2%, weather +1.5%)",
  "implied_multiples_after_fix": {"pe": 10.4, "pb": 1.17, "ev_ebitda": 6.4}
}
```

This makes the fix auditable and surfaces in future runs whether assumptions have drifted back into unrealistic territory.

## Step 9: Update Index Page

**Always run after dashboard generation.**

Update the stock index at `./index.html` to include this ticker. The index contains a JavaScript `stocks` array with entries like:

```js
{ ticker: "AAPL", name: "Apple Inc.", sector: "Technology", dashboard: true, metrics: true, analysis: true, dcf: true }
```

1. Read `./index.html` and check if `$ARGUMENTS` already exists in the `stocks` array
2. If not present, add a new entry with:
   - `ticker`: `$ARGUMENTS`
   - `name`: Company name from the Analysis JSON
   - `sector`: Short sector label from the Analysis JSON
   - `dashboard`, `metrics`, `analysis`, `dcf`: `true`/`false` based on which report files exist
3. Insert the entry in **alphabetical order by ticker** (numbers before letters, e.g. "0285.HK" before "ASML")
4. Update the subtitle count: find `${stocks.length} companies tracked` - the template literal auto-updates, so no change needed
5. If already present, update the `name` and `sector` fields in case they changed

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
- [ ] DCF sanity check ran (Step 8c) and `sanity_check` block populated in DCF JSON
- [ ] If sanity check tripped: fix applied, warning banner present in dashboard, `valuation_philosophy` block populated

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

**Index Page:**
- [ ] index.html updated with new/updated ticker entry
- [ ] Entry is in correct alphabetical position in the stocks array

**Note:** To force re-download of ALL reports (not just new ones), delete the PDFs folder:
```bash
rm -rf "./$ARGUMENTS/PDFs"
```
