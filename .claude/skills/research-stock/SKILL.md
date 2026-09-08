---
name: research-stock
description: Downloads financial reports from company IR website, extracts data, and creates a CSV-backed HTML dashboard for stock research
allowed-tools: Bash, Read, Write, Edit, WebFetch, WebSearch, Glob, Grep, Task
argument-hint: "[TICKER]"
# Orchestration is mkdir/ls/spawn work; Fable here cost $3.89 of a $13 TPW.AX run.
# Matches BATCH_MODEL in scripts/lib.sh; valuation stays on fable via dcf-analyst.md.
model: claude-opus-5
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

**Non-US tickers — run the deterministic fetcher first, no agent:**
```bash
make fetch-filings TICKER=$ARGUMENTS
```
It dispatches on the suffix — `.AX` to the ASX per-year announcement listings, `.NZ`
to nzx.com's hydration and per-year listing API, `.HK` to HKEXnews' title search —
and seeds a thin `.L`/`.AX`/`.NZ` ticker from the AnnualReports.com archive as well.
Everything it downloads is staged and must pass a deterministic gate (real PDF,
≥30 KB, extractable text, the company's own name in that text, no overwrite) before
it reaches `research/{T}/PDFs/`; survivors are extracted to `Extracted/` in the same
pass, so Step 3 and Step 4 have less to do. Files that exist are skipped, and the
run prints `planned / downloaded / promoted / quarantined` with a reason per reject.
Add `DRY=1` to see the plan without downloading.

Exit codes: **0** ran cleanly (even if nothing was new), **1** the adapter failed,
**3** the deadline was spent. Only spawn the ir-scraper afterwards for what it
reports as missing (investor presentations, a report filed under an unusual title)
or when it exits 1 — on TPW.AX the scraper burned 31 turns, and on SMI.NZ 3.5
minutes, doing by hand what this does in one call. US filers have no adapter here:
they take the SEC XBRL route (Step 4).

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

Writes to `./research/$ARGUMENTS/Reports/$ARGUMENTS.duckdb`, then runs
`scripts/adjudicate.py`, which resolves what the candidates already settle and
writes `./research/$ARGUMENTS/Reports/$ARGUMENTS_Worksheet.md` -- the grid of
resolved / contested / missing cells the agent reviews. Takes a few seconds,
no model.

**This must run before the financial-parser agent is spawned.** The agent
queries the facts table instead of grepping the filings — that search
previously took 183 model turns and 18.2M cache-read tokens on a single
ticker, roughly 60% of its total cost.

### Step 5b: Adjudicate with the financial-parser agent

The agent (`.claude/agents/financial-parser.md`) reviews the worksheet --
accepting resolved cells, deciding contested and definition-sensitive ones,
opening filings only at the statement line ranges the worksheet names --
determines units and currency, and writes **both** the `core_metrics` table and
the exported CSV. Tell it the worksheet path in the task prompt
(`./research/$ARGUMENTS/Reports/$ARGUMENTS_Worksheet.md`); it should not grep
the filings. `core_metrics` uses a fixed schema shared by every ticker in
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

## Step 7: Dashboard — deferred to after Step 8c

The dashboard embeds the DCF, so it is rendered **once, after the valuation and its
sanity check** (Step 9a below). Do not build it here and do not hand-edit HTML at any
point: `scripts/build_dashboard.py` renders the page from a Spec JSON.

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

```bash
python3 scripts/refresh_price.py --ticker $ARGUMENTS --apply
```

It fetches the live quote, rewrites every price-derived number (root `current_price`,
every nested `current_price`, `market_data.price`, the scenario upsides, entry
discounts and the weighted upside) surgically, and refuses on a currency or
denomination mismatch. Prose that quotes the old price is listed under
`price_refresh.prose_paths_quoting_previous_price` — fix those sentences only if the
claim they make has flipped. Do not recompute any of this by hand.

## Step 8c: DCF Sanity Check (Implied Multiples vs History)

**Always run after Step 8b.** This step exists because DCF models can produce intrinsic values implying multiples the market has never paid. SEK.NZ (Seeka) was the canonical failure: a peak-FCF DCF produced IV $22.75 implying 30x P/E and 3.4x P/B, despite Seeka never trading above 0.99x P/B or 7.8x EV/EBITDA in 10 years.

### Steps 8c.1-8c.4: Run the check

```bash
python3 scripts/sanity_check.py $ARGUMENTS --apply
```

That is the whole of 8c.1 through 8c.4, and it writes the 8c.7 block. Do
**not** fetch prices, compute multiples or apply the trip rules by hand:
this used to be ~10 turns of an Opus orchestrator per ticker, and every one
of those steps is arithmetic with one right answer.

What it does, so you can read the output: 10 years of monthly closes
(cached to a committed `{TICKER}_Prices.csv`), FY-end prices aligned to the
annual rows from `metrics_normalized`, historical P/E, P/B, EV/EBITDA and
P/Sales **on an SBC-adjusted basis on both sides**, the implied multiples of
the base-case IV on the latest FY actuals, the four trip rules, and the
8c.4 diagnosis candidates. A year whose `StockBasedComp` is missing gets
`null` adjusted multiples rather than an unadjusted number, so an adjusted
implied multiple is never compared against a half-unadjusted average.

**Exit codes:**

- **0** — clean, or a model whose trip rules do not apply (AFFO, NAV/NTA,
  book-value, earnings-at-CoE, risked-NPV: `passed` is `null` with a
  `checks_replaced` note). Nothing more to do; go to Step 9.
- **2** — refused. Either there is no DCF, or the quote currency differs
  from the reporting currency with no `inputs.fx_rate` to bridge them
  (WISE.L's GBp quote against USD filings). Fix the input and re-run.
- **3** — tripped. Spawn the `dcf-analyst` **once** with the printed
  `trip_reasons` and `diagnosis` candidates and the instruction in 8c.5
  below: adjust the drivers, rebuild the DCF, record `fix_applied` and
  `implied_multiples_after_fix` in the `sanity_check` block, and stop. Do
  not loop; one fix pass is the budget.

The script preserves any existing `fix_applied` / `implied_multiples_after_fix`
across re-runs, so the agent's record of what it changed stays auditable.

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

Already done — `scripts/sanity_check.py --apply` wrote the block. It
rewrites only the `sanity_check` key, leaving the rest of the file
byte-for-byte, so the diff shows the one thing that changed.

The block carries `ran`, `passed`, `implied_multiples`,
`historical_averages_ex_outliers` (with `pb_max` and `ev_ebitda_max`, the
figures the trip rules actually use), `trip_reasons`, `diagnosis`, the
per-FY `historical_table`, and `computed_by` / `computed_at`.

The only fields you add by hand are the two that record **your** judgment
after a trip: `fix_applied` and `implied_multiples_after_fix`. Both survive
subsequent re-runs of the script.

These multiples are SBC-adjusted on both sides and so deliberately will not
match Yahoo's or a screener's GAAP P/E — the block says as much in `basis`,
so a future reader does not "correct" it back.

## Step 9a: Generate the Dashboard (no model, then an optional tweak)

```bash
make dashboard-spec TICKER=$ARGUMENTS      # default Spec from templates + populated columns
make dashboard TICKER=$ARGUMENTS           # render Reports/{TICKER}_Dashboard.html
python3 scripts/kpi_coverage.py $ARGUMENTS # which stored KPIs are stranded
```

`dashboard_spec.py` routes the ticker to a business-model template (SaaS, payments,
e-commerce, REIT, bank, LIC, retirement-village, pre-revenue miner, or plain
operating) via `scripts/sectors.py`, keeps only the cards and charts whose columns
are populated, and writes company-substituted help text. If a Spec already exists
(an agent edited it on an earlier run) it is kept; pass `FORCE=1` to regenerate.

Spawn the `dashboard-generator` agent **only** when `kpi_coverage.py` lists
`unmapped` or `promoted` KPIs the default Spec did not chart, or the business has a
driver the templates cannot know (a segment split, a disclosed unit-economics
series). It edits the existing Spec — it never authors one and never reads another
ticker's dashboard — then re-runs `make dashboard`. On 40 measured runs the old
author-everything agent cost $1.64 a ticker and spent 28 turns reading other
tickers' dashboards.

Check the `slider engine:` line the build prints. `FALLBACK` means the DCF JSON's
assumptions do not rebuild its valuation (the sliders use a generic scaler); it is
not a dashboard error, and only the dcf-analyst can fix it.

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
