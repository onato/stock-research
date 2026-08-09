---
name: backfill-msn
description: Fill gaps in a ticker's Metrics CSV from MSN Money, after validating the third-party figures against the filings-derived data already there. Use when `make missing` shows holes -- especially CapEx, OperatingCashFlow or FreeCashFlow -- and re-extracting the filings has not filled them.
allowed-tools: Bash, Read, Edit, Glob
argument-hint: "TICKER [TICKER ...] [--apply]"
---

# Backfill missing metrics from MSN Money

Fill holes in `research/{TICKER}/Reports/{TICKER}_Metrics.csv` using MSN's
annual financials, **only where the third-party figure can be shown to agree
with the filings-derived data already in the file**.

Default mode is **propose**: print the proposed writes and stop. Only apply
when `$ARGUMENTS` contains `--apply`.

## Why this source, and why the caution

The corpus's biggest gap is CapEx -- 271 of 896 missing core-8 cells -- and
the extractors do not reliably produce it. MSN publishes it per fiscal year,
along with operating cash flow and the rest.

But this is vendor data, not a filing. The derived database at
`~/Stocks/rule1/data/financial_data.duckdb` disagrees with our filings on
17-22% of comparable cells (EPS on 75%), so **a figure that has not been
checked against something we already trust does not go in the CSV.** The raw
API is much better than that derived database -- PINS and AIA.NZ matched at
exactly 1.0000 on every comparable cell -- but "much better" is not "verified
for this ticker".

The rule this skill exists to enforce, from `CLAUDE.md`: a missing row is
obvious, a plausible wrong one is not.

## Step 1: See what is actually missing

```bash
make missing TICKER={TICKER} FORMAT=csv
```

Note which fields and which periods. If the gaps are all outside the ticker's
reporting span (`out_of_scope_years`), stop -- there is nothing to fill.

## Step 2: Fetch (cached)

```bash
uv run python3 scripts/msn_fetch.py {TICKER}
```

Payloads cache to `state/msn_cache/{TICKER}.json` for 30 days. MSN rate
limits, so do not pass `--refresh` unless the cached copy is genuinely stale.
For several tickers, pass them in one invocation -- the rate limiter spaces
calls within a run.

If MSN returns nothing, stop and say so. Some tickers are not covered.

## Step 3: Validate before writing anything

Four checks. **Any failure disqualifies the ticker** -- report it and move on;
do not "fix" the data to make it fit.

1. **Currency.** Compare MSN's `Currency` against the `Currency` column in the
   CSV. They must match. BYD (1211.HK) comes back tagged `BGN` -- Bulgarian
   Lev -- for a company reporting in CNY; that ticker is refused, not
   converted. `msn_fetch.currency_conflict()` does this comparison.

2. **Scale.** `msn_fetch` returns millions, matching the repo convention, but
   confirm against the CSV's `Units` column. Per-share figures are the trap:
   MSN reports EPS in currency units (AIA.NZ FY2007 EPS = 0.21) while some
   CSVs store cents (AIA.NZ FY2025 EPS = 25.87). **Never backfill EPS without
   confirming the scale of that specific ticker's column** -- and note that
   AIA.NZ's own column is internally inconsistent (FY2024 is 0.37, FY2025 is
   25.87), which is a bug to log via `make gaps`, not to propagate.

3. **Overlap agreement, per field -- not per ticker.** For every period where
   the CSV *already has* a value and MSN also has one, compute the ratio.
   **A field is eligible only if every overlapping cell for that field agrees
   within 2%** (allow one mismatch where there are 8+ overlaps). Judge each
   field on its own evidence: a ticker-level average hides exactly the case
   that matters. PINS scores 88% overall, but that single number conflates
   Revenue, NetIncome, OperatingCashFlow and TotalAssets at 100% with
   ShareholdersEquity failing outright -- filling on the average would be
   wrong in both directions.

   Report the per-field table. A field that fails is disqualified for that
   ticker; the others may still proceed.

   **Sign conventions differ and are not disagreements.** PINS CapEx reads
   28.984 in our CSV and -28.98 from MSN: identical magnitude, opposite sign,
   because the CSV follows the filing's presentation. Compare magnitudes for
   CapEx, then **write it in the sign the ticker's existing column already
   uses** -- look at a populated CapEx cell in that CSV and match it. Getting
   this backwards silently flips FreeCashFlow.

   **A definitional difference is a refusal, not a rounding issue.** AIA.NZ's
   CapEx differs from MSN's by 15-25% in FY2021-25 -- they are not deducting
   the same things, and the project memory records that AIA needs a component
   owner-FCF treatment. Do not fill a field whose overlaps differ
   consistently in one direction, however "close" it looks.

4. **Fiscal-year alignment.** For non-December year-ends, confirm MSN's `year`
   means the same period ours does -- compare a couple of populated years'
   revenue. Apple's September year-end is misaligned in the derived database;
   check rather than assume.

## Step 4: Propose

Print a table: ticker, period, field, proposed value, and the agreement rate
that justifies it. Say explicitly which fields you are *not* filling and why
(EPS scale unconfirmed, no MSN data, currency conflict).

Never propose to overwrite a cell that already has a value. This skill fills
holes; a disagreement on an existing value is a finding to report, not an
edit to make. Where MSN contradicts a populated cell, log it:

```bash
uv run python3 scripts/log_gap.py --ticker {TICKER} --metric {FIELD} \
  --kind value_conflict --detail "MSN says X, CSV says Y for FY20NN"
```

## Step 5: Apply (only with `--apply`)

Edit the CSV directly, filling only the proposed cells. Then:

```bash
make missing TICKER={TICKER}     # the gaps should have shrunk
make integrity                   # nothing should regress
make test                        # CSVs feed the tests
```

Commit the data change on its own, with the agreement rate in the message and
`source: MSN Money` stated plainly, so a later reader knows those cells are
not filings-derived:

```
data: backfill CapEx for {TICKER} from MSN Money

N cells across FY20XX-FY20YY. Validated against the M overlapping cells
already in the CSV: agreement within 2% on P% of them, currency NZD on both
sides. Not filings-derived -- MSN Money, fetched YYYY-MM-DD.
```

## Notes

- **Do not backfill EPS or SharesOutstanding by default.** EPS has the cents
  trap above; MSN does not publish a share count this module reads.
- FreeCashFlow from MSN is derived as `OperatingCashFlow - |CapEx|`. If the
  ticker's DCF uses a different owner-FCF definition (AIA.NZ, 2CC.NZ and TAH.NZ
  all deduct lease principal per the project memory), do not fill FCF -- fill
  OCF and CapEx and let the DCF derive it.
- The `.duckdb` files are caches; this changes CSVs only. Rebuild with
  `make facts TICKER={TICKER}` afterwards if anything reads the DB.
