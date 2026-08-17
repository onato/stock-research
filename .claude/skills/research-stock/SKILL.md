---
name: research-stock
description: Downloads financial reports from company IR website, extracts data, and creates a CSV-backed HTML dashboard for stock research
allowed-tools: Bash, Read, Write, Edit, WebFetch, WebSearch, Glob, Grep, Task
argument-hint: "[TICKER]"
---

# Stock Research Workflow

You are researching the stock: $ARGUMENTS

Create the directory structure first:
```bash
mkdir -p "./research/$ARGUMENTS/PDFs" "./research/$ARGUMENTS/Extracted" "./research/$ARGUMENTS/Reports"
```

## Step 0: Check for Existing Data

**Before downloading anything, check what data already exists:**

```bash
# List existing PDFs
ls -la "./research/$ARGUMENTS/PDFs/" 2>/dev/null || echo "No PDFs folder yet"

# List existing extracted text
ls -la "./research/$ARGUMENTS/Extracted/" 2>/dev/null || echo "No Extracted folder yet"

# List existing reports
ls -la "./research/$ARGUMENTS/Reports/" 2>/dev/null || echo "No Reports folder yet"
```

**Skip downloading if reports already exist:**
- If `./research/$ARGUMENTS/PDFs/` contains PDF files, **DO NOT re-download** them
- If `./research/$ARGUMENTS/Extracted/` contains .txt files, **DO NOT re-extract** them
- Only download reports that are missing (e.g., newer quarters/years)

**When to re-download:**
- No PDFs exist yet
- User explicitly asks to refresh/update the data
- A new quarter/year has been released since last download

## Step 1: Find Investor Relations Website

**Skip this step if PDFs already exist in `./research/$ARGUMENTS/PDFs/`**

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

Save PDFs to: ./research/$ARGUMENTS/PDFs/

Download using curl:
```bash
curl -L -o "./research/$ARGUMENTS/PDFs/{filename}" "{url}" \
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

**Skip if .txt files already exist in `./research/$ARGUMENTS/Extracted/` for each PDF**

Only extract text for PDFs that don't have a corresponding .txt file yet.

Run pdftotext to extract readable text:
```bash
pdftotext -layout "./research/$ARGUMENTS/PDFs/{input}.pdf" "./research/$ARGUMENTS/Extracted/{output}.txt"
```

Store extracted text in: ./research/$ARGUMENTS/Extracted/

## Step 5: Parse Financial Metrics

**Always regenerate** - may include data from newly downloaded reports.

### Step 5a: Build the facts table first (fast, no model)

```bash
python3 scripts/extract.py "$ARGUMENTS"
```

This picks the extraction path for the ticker's exchange:

- **US filers** (bare symbol, no suffix) → SEC XBRL `companyfacts`. Returns
  typed facts with exact periods and units, written straight to
  `core_metrics`. No adjudication needed — the data is already resolved.
- **Everything else** (`.NZ`, `.AX`, `.HK`, `.L`, …) → text extraction from
  `Extracted/*.txt` into the `facts` table, for the agent to adjudicate.

It falls back from XBRL to text automatically when SEC has no coverage (ADRs,
foreign private issuers), and logs a gap when neither path produces anything.

Writes to `./research/$ARGUMENTS/Reports/$ARGUMENTS.duckdb`. Takes ~1 second.

**This must run before the financial-parser agent is spawned.** The agent
queries the facts table instead of grepping the filings — that search
previously took 183 model turns and 18.2M cache-read tokens on a single
ticker, roughly 60% of its total cost.

### Step 5b: Adjudicate with the financial-parser agent

The agent (`.claude/agents/financial-parser.md`) resolves competing candidates,
determines units and currency, and writes **both** the `core_metrics` table and
the exported CSV. `core_metrics` uses a fixed schema shared by every ticker in
this repo (see `scripts/schema.py`), which is what makes cross-ticker
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

Write metrics to CSV: ./research/$ARGUMENTS/Reports/{TICKER}_Metrics.csv

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

### Step 5c: Extraction gate — check the data before spending on agents

```bash
python3 scripts/run_evals.py "$ARGUMENTS"
```

This is a **gate, not a report**. Steps 6-8 cost real money (a full run is
$3.50–$4.91), and every one of them is built on this CSV — a DCF computed
from a broken extraction is expensive and worthless. Read the output before
continuing.

**Stop and fix the extraction if you see any of these:**

| Check | What it means |
|---|---|
| `csv_parse` | the CSV is missing or empty — the parser wrote nothing |
| `periods_unique` | the same period twice; one is a duplicate or mislabelled row |
| `essential_coverage` | a field the DCF consumes has no values at all |
| `units_consistent` | DB and CSV disagree by ~1000x — a units bug |

`essential_coverage` is the one that catches a *thin* extraction rather than a
malformed one. It grades `net_income`, `shareholders_equity` and
`shares_outstanding` per-field, because a column can be present in the header
and empty in every single row — that is how AAPL ended up with no share count
and PNG.V with none either, both scoring a clean 1.0 beforehand. `revenue` is
graded as a warn only, since NAV vehicles (BIF.NZ, FIH.U, BGI.NZ) have no
revenue line by design.

When it fails, the fix is upstream — go back to Step 5a/5b and find why the
field is empty (missing filings, a label the parser does not recognise, an
adjudication that dropped the row). If the gap is genuinely unavailable from
the filings, `/backfill-msn` can fill it from MSN Money after validation.

**Warns are a review queue, not a blocker.** `identity_fcf`, `eps_share_scale`
and the `continuity_*` checks all have documented legitimate causes
(owner-FCF adjustments, EPS-in-cents tickers, share consolidations). Read
them, satisfy yourself each one is explained, and continue — do not "fix" a
warn that is describing correct data.

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

Write the analysis to: ./research/$ARGUMENTS/Reports/{TICKER}_Analysis.json

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

Output: ./research/$ARGUMENTS/Reports/{TICKER}_Dashboard.html

## Step 8: DCF Valuation

**Always regenerate** - DCF contains current price and valuation data that should be refreshed each run.

Create a DCF valuation model based on the financial data and qualitative analysis.

Spawn the `dcf-analyst` agent (`.claude/agents/dcf-analyst.md`).

**Do not restate the valuation method here.** The agent routes the ticker to the right
model via `.claude/skills/dcf-methods/SKILL.md` and reads that model's reference file —
an owner-FCF DCF for operating companies, and something else entirely for banks, REITs,
LICs, holdcos and distressed shells. Duplicating the method in this file is how the two
drift apart: this step previously carried a share-count rule the agent no longer uses.

Outputs:
- `./research/$ARGUMENTS/Reports/{TICKER}_DCF.json` — embedded in the dashboard
- `./research/$ARGUMENTS/Reports/{TICKER}_DCF_Model.xlsx` — downloadable Excel model

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

2. Read `current_price` from `./research/$ARGUMENTS/Reports/{TICKER}_DCF.json`

3. If the prices differ by more than 5%, update the DCF JSON:
   - Set `current_price` to the Yahoo Finance price
   - Recalculate `upside` for all three scenarios: `((intrinsic_value / new_price) - 1) * 100`
   - Recalculate `probability_weighted.weighted_iv` is unchanged (it's based on intrinsic values, not current price) — but verify the `entry_price.base.entry_discount_from_current` is updated: `((current_price - entry_price) / current_price) * -100`. The `entry_price` value itself does not depend on current price, so it never needs recomputing on price drift.
   - Write the updated JSON back to the file

4. If the prices match within 5%, no changes needed — log that the price was verified.

5. **If the DCF JSON was updated**, re-embed the updated DCF JSON into the dashboard HTML (`./research/$ARGUMENTS/Reports/{TICKER}_Dashboard.html`) by replacing the existing `const dcfData = {...};` block with the corrected data. This ensures the dashboard displays the verified price.

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

## Step 9: Update Company Registry + Index Page

**Always run after dashboard generation.**

`./index.html` is GENERATED output (the searchable screener leaderboard,
written by `.claude/skills/screen-investments/screen.py --html`) — never edit
it directly. Company names and sectors live in `./state/companies.json`:

```json
{ "AAPL": { "name": "Apple Inc.", "sector": "Consumer Electronics / Services" } }
```

1. Read `./state/companies.json` and check if `$ARGUMENTS` has an entry
2. Add or update it: `name` = company name from the Analysis JSON, `sector` =
   short sector label from the Analysis JSON (keys stay alphabetically sorted)
3. Also write/refresh `./research/$ARGUMENTS/info.json` (the fetcher's curated
   metadata): set `name`, `sector`, `fiscal_year_end` ("MM-DD", from the
   filings), `updated_by: "claude"`, `needs_review: false` — and PRESERVE any
   existing fields (`quirks`, `ir_url`, `aliases`). Merge, never overwrite.
4. Regenerate the index without hitting the network:

```bash
python3 .claude/skills/screen-investments/screen.py --html "$(pwd)/index.html"
```

(Stored prices are fine here — the weekend screener refreshes live prices.)

## Never end your turn with work still running

**Every command must finish before you move on.** Do not background a slow
step and end the turn expecting it to continue — the process is killed when
the run ends, so the work is simply lost.

These runs each exited cleanly, reported success, cost $3.50–$4.91, and
produced no metrics at all:

- *"The report scraper is still downloading the remaining filings in the
  background. I'll continue the pipeline…"* — 28 PDFs, 0 extracted
- *"The metrics extractor is still processing the ~16MB of filing text in
  the background; I'll launch the financial parser as soon as it finishes."*
- *"OCR is on the final file (FY2017, mid-write). Waiting for it to
  complete before building the facts table."*
- *"The financial-parser and qualitative-analyst agents are still running
  in the background — I'll continue the pipeline as soon as their results
  arrive."* — APL.NZ: the harness terminated them at its 600s background
  ceiling; no core_metrics, no CSV, no DCF, no dashboard.

**This applies to subagents too.** Spawn financial-parser and
qualitative-analyst with `run_in_background: false` — issuing both Agent
calls in ONE message still runs them concurrently, so nothing is lost by
waiting. Never end a turn while a subagent is pending: in a headless batch
there is no later turn for its results to arrive in.

If a step is genuinely slow, **wait for it** — run it in the foreground and
let it take the time. If something cannot complete, say so plainly and stop;
a truthful failure is recoverable, a false success is not.

Before ending, confirm all three deliverables exist:

```bash
ls research/$ARGUMENTS/Reports/${ARGUMENTS}_Metrics.csv \
   research/$ARGUMENTS/Reports/${ARGUMENTS}_DCF.json \
   research/$ARGUMENTS/Reports/${ARGUMENTS}_Dashboard.html
```

## Exit Gate: the run is not done until the eval passes

```bash
python3 scripts/run_evals.py --strict "$ARGUMENTS"
```

`--strict` exits non-zero if any check failed. **A non-zero exit means the run
is not finished** — fix what it names and re-run it, or say plainly that the
ticker is left in a broken state. Do not report success over a failing gate.

This exists because the failure mode above is not hypothetical: runs have
exited cleanly, reported success and cost $3.50–$4.91 with no metrics at all.
A self-assessed checklist did not catch those; an exit code does. It also
covers the checklist items that are mechanically checkable — the dashboard
existing (AIR.NZ once scored 1.0 while missing one, which is why that check
is a `fail` and not a `warn`), the DCF's `sanity_check` block being populated,
the scenario weights summing to 1, and the weighted IV actually recomputing
from the scenario IVs.

The scorecard is written to `state/scores/{TICKER}_{date}.json` with the agent
prompt hash, so a score change is attributable to a prompt version.

## Final Checklist

**Data Collection (cached - only download new reports):**
- [ ] Check for existing PDFs in ./research/$ARGUMENTS/PDFs/
- [ ] Download only NEW quarterly/annual reports not already present
- [ ] Rename any new files to standard format
- [ ] Extract text only for new PDFs (skip existing .txt files)

**Analysis (regenerate each run for fresh data):**
- [ ] Metrics CSV rebuilt from all extracted text (includes any new data)
- [ ] Metrics CSV includes DCF fields: ShareholdersEquity, TotalDebt, CashAndEquivalents, SharesOutstanding
- [ ] Extraction gate (Step 5c) ran and passed **before** the paid agents were spawned
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

**Exit gate (deterministic — this one is not self-assessed):**
- [ ] `run_evals.py --strict $ARGUMENTS` exits zero, or the failure is stated plainly

**Note:** To force re-download of ALL reports (not just new ones), delete the PDFs folder:
```bash
rm -rf "./research/$ARGUMENTS/PDFs"
```
