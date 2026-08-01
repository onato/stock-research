# Eval Framework — making the research measurably better over time

The pipeline already measures **cost** (`cost_report.py`, `cost_baseline.json`).
This adds the other axis: **quality**, scored per run, trended over time, and
attributable to the version of the agent prompts that produced it. The goal is
that every edit to `.claude/agents/*.md` can be answered with a number instead
of a vibe.

"Better research" decomposes into three tiers, ordered by objectivity and by
how long the signal takes to arrive. Cheap-and-instant runs on every pipeline
run; slow-but-true accrues in a ledger and pays off in 6–24 months.

---

## Tier 1 — Deterministic correctness (free, instant, every run)

Pure-Python checks, no LLM calls. Run after every research run and fail loudly.
Output: `scorecard.json` per (ticker, run).

### 1a. Extraction integrity (`core_metrics` in the ticker DuckDB)
- **Accounting identities**, each within tolerance (e.g. 2%):
  - `FreeCashFlow ≈ OperatingCashFlow − CapEx`
  - `GrossMargin ≈ GrossProfit / Revenue`, `NetMargin ≈ NetIncome / Revenue`
  - `TotalAssets ≈ TotalLiabilities + ShareholdersEquity` (when all present)
  - `EPS ≈ NetIncome / SharesOutstanding` (basic-vs-diluted slack)
- **Continuity checks** (catch unit drift and split mishandling — the AMZN ÷20,
  BYD 3x, NFLX 10:1 class of bug):
  - Revenue / shares / equity should not jump >5x between adjacent periods
    without a `kpis`/notes explanation.
  - Period labels parse and sort; no duplicate periods; FY vs H1/Q consistent.
- **Coverage**: fraction of core columns non-null per period; # periods vs
  filings present in `PDFs/`. A parser "improvement" that silently drops
  columns should show up as a coverage regression.

### 1b. DCF internal consistency (`{TICKER}_DCF.json`)
- Recompute and compare: `upside = IV / current_price − 1`;
  `weighted_iv = Σ weight_s × IV_s`; weights sum to 1.0.
- Entry price satisfies the hurdle-IRR formula (guards against regressions of
  the 2026-07-21 entry-price bug — encode the fixed formula as the oracle).
- `sanity_check.ran == true` and `passed`, or `trip_reasons` non-empty.
- Flag `terminal_pct_of_value` above threshold (e.g. >80%) as a warning.

### 1c. Policy lint — memory lessons as executable checks
Every hard-won rule in memory becomes a check, so it can never silently
regress:
- SBC: `inputs.sbc` non-null, or an explicit justification field (2CC "verified
  NIL" pattern).
- Component-built margins: projections carry per-year component paths, not one
  flat margin copied forward (post-2026-07-29 policy).
- Ticker-specific rules from memory files where machine-checkable (e.g.
  BABA net-cash netting present; SUM.NZ not using OCF as FCF).

### 1d. Pipeline health
- Dashboard smoke test: HTML references the CSV; CSV headers ⊆ schema
  `CSV_HEADERS` + known KPI names; Papa-parseable (no ragged rows).
- `Extracted/*.txt` non-trivial (bytes threshold) for each PDF.

---

## Tier 2 — Golden set + rubric judge (cheap, on demand)

Runs when an agent prompt changes, or weekly. This is the **validation gate**:
no edit to `.claude/agents/*.md` lands unless the golden-set score is ≥ the
current best, and no ticker regresses.

### 2a. Golden extraction set
Hand-verify `Metrics.csv` for ~5 deliberately diverse tickers (one per
filing-regime the parser must handle):

| Ticker | Regime it exercises |
|---|---|
| AMZN or V | US EDGAR iXBRL, splits, buybacks |
| AGL.NZ or 2CC.NZ | NZX PDFs, small-cap, missing line items |
| AFI.NZ | LIC / NTA model, AUD-in-NZD quote |
| SRBK | bank (no meaningful FCF), residual-income |
| FRFHF | insurer/holdco, BVPS model, IFRS-17 break |

Store as `evals/golden/{TICKER}_Metrics.golden.csv`. Score the parser
per-cell: precision/recall of non-null cells vs golden, values within 1%.
One number per run: **cell accuracy**. This is the reward function any
optimizer (manual or skillopt) needs.

### 2b. Qualitative rubric judge
`Analysis.json` can't be diffed against ground truth, so use a pinned LLM
judge (fixed model + fixed rubric prompt, versioned in this folder) scoring
1–5 on:
- **Specificity**: named competitors, real numbers, dated events — vs
  boilerplate ("competitive industry", "regulatory risk").
- **Risk coverage**: does it hit the known risk list for that ticker (seeded
  from memory files)?
- **Consistency**: claims don't contradict `core_metrics` (e.g. "strong FCF
  growth" while FCF CAGR is negative).
Trend only same-judge-version scores against each other.

---

## Tier 3 — Outcome calibration (slow, the only real ground truth)

A DCF is a forecast; forecasts are scored by outcomes.

### Prediction ledger
Append-only `evals/ledger.jsonl`, one row written every time a DCF is
produced (DCF.json gets overwritten on re-research; the ledger never does):

```json
{"date": "2026-08-01", "ticker": "AGL.NZ", "price": 0.166,
 "iv": {"bear": -0.10, "base": ..., "bull": 0.084}, "weights": {...},
 "weighted_iv": ..., "entry_price": ..., "verdict": "avoid",
 "agents_sha": "<git rev-parse HEAD:.claude/agents>"}
```

`agents_sha` ties every prediction to the prompt version that made it.

### Scoring (6 / 12 / 24 months later)
`MonthlyPrices.txt` infrastructure already fetches prices; a scorer walks the
ledger and computes:
- **Directional hit rate**: did names called undervalued (price < weighted IV
  with margin) outperform names called avoid, and a benchmark?
- **Calibration**: outcomes should land inside the bear–bull band roughly as
  often as the weights imply. Persistent misses on one side ⇒ systematic
  optimism/pessimism — the single most valuable feedback for the DCF prompt.
- **Entry-price discipline**: for tickers that hit the computed entry price,
  realized IRR vs the hurdle rate.

This tier is why the ledger must start **now** — it's the only part that can't
be backfilled later.

---

## Wiring & trend tracking

Everything runs locally — no CI dependency. (The `.github/` prefix on paths is
just where scripts/state already live; nothing here needs Actions.)

- `run_evals.py {TICKER}` → writes `state/scores/{TICKER}_{date}.json`
  containing tier-1 results (+ tier-2 when run), `agents_sha`, and cost from
  `cost_report.py` — so quality-per-dollar is one join.
- `research_one.sh` calls it as the last step, so `run_local.sh` /
  `run_loop.sh` runs are scored automatically; scorecards are committed with
  the run's other artifacts.
- A small `trend_report.py` (later) groups scores by `agents_sha` to show
  whether a prompt change actually helped.

## Verdict on microsoft/skillopt

Not useful today, but this framework is exactly its missing prerequisite, and
one stage is a genuine future fit.

- skillopt's loop (rollout → score → bounded text edit to the skill → accept
  only if held-out validation improves) **requires a scoring function and many
  cheap rollouts**. Full research runs cost $1–11/ticker with an
  8-runs/weekend budget — far too expensive/slow to be skillopt rollouts.
- **What to steal now, manually ("skillopt-lite")**: (1) the agent `.md` files
  are the trainable parameter — already true here; (2) the held-out validation
  gate — never accept a prompt edit unless the tier-2 golden score improves
  with no per-ticker regression; (3) bounded edits — one small prompt change
  per iteration, so score deltas are attributable.
- **Where skillopt could genuinely run later**: the financial-parser stage in
  isolation. Its inputs (`Extracted/*.txt`, `facts` table) are fixed on disk,
  its output is objectively scorable against the golden CSVs (tier 2a), and a
  rollout is one subagent call, not a full pipeline. Once the golden set
  exists, pointing skillopt (or a hand-rolled loop) at
  `.claude/agents/financial-parser.md` with cell-accuracy as the reward is a
  realistic weekend project. Revisit then.

## Build order

1. **DONE (2026-08-01)** Prediction ledger — `scripts/ledger.py`, appends to
   `evals/ledger.jsonl` (67 rows backfilled). `make ledger-backfill`.
2. **DONE (2026-08-01)** Tier-1 checks — `scripts/run_evals.py` (+ shared
   `scripts/dcf_fields.py`), scorecards to `state/scores/`, wired into
   `research_one.sh` before the commit step. `make evals TICKER=X` /
   `make evals-all`. First sweep: V's weighted IV doesn't recompute (stale
   Feb-2026 DCF) and CSU has no current_price; `policy_sbc` warns mark the
   ~55 DCFs predating the 2026-07-29 SBC policy.
3. Golden set for 2 tickers (one EDGAR, one NZX), expand to 5.
4. Rubric judge; then the skillopt-lite gate for prompt edits.
