---
name: refresh-stock
description: Cheap narrative + valuation refresh for a ticker whose financials are already current. Skips download, extraction and financial parsing; runs only the qualitative and DCF stages. Use for a tier-2 ticker (stale by date, no new filings) instead of a full re-research.
allowed-tools: Bash, Read, Write, Edit, WebFetch, WebSearch, Glob, Grep, Agent
argument-hint: "[TICKER]"
---

# Tier-2 Refresh — $ARGUMENTS

This ticker is **stale by date only**. Its filings are already parsed and its
Metrics CSV is already current; what has decayed is the narrative (recent
developments, competitive position, risks) and the valuation built on top of
it.

A full `/research-stock` here costs ~32 min and ~$5–8 because it carries four
"Always regenerate" directives and fires every heavyweight subagent. Measured
on this corpus: of 20 tickers stale by `valuation_date`, 19 had no new filings
at all. Re-running the parser on those re-derives numbers that provably cannot
have moved — the same filings, through the same parser, produce the same CSV.

So this skill does the **narrative and valuation half only**.

## Step 0: Verify the tier-2 precondition — REQUIRED

This skill is only correct when there is genuinely nothing new to parse.
Confirm that before spending anything:

```bash
uv run python3 scripts/refresh_plan.py --ticker $ARGUMENTS
```

- **tier 2** → proceed. This is the case this skill exists for.
- **tier 3** → **STOP.** There is an unparsed filing, or no DCF at all. The
  parser must run; use `/research-stock $ARGUMENTS` instead. Say so and stop
  — do not attempt to refresh a valuation around data you did not extract.
- **tier 0 or 1** → **STOP.** Nothing needs a model. `make refresh-price
  APPLY=1 TICKER=$ARGUMENTS` handles a price move for free.

Report the tier you saw before continuing.

## Step 1: Confirm the inputs exist

The refresh builds on the existing CSV. If it is missing, this is not a
tier-2 ticker:

```bash
ls research/$ARGUMENTS/Reports/${ARGUMENTS}_Metrics.csv
```

Read it, plus the current `${ARGUMENTS}_DCF.json`. You need the existing
valuation's assumptions to know what the narrative refresh might change.

**Do NOT** run `scripts/extract.py`, `scripts/build_facts.py`, the
`ir-scraper`, `pdf-processor`, or `financial-parser` agents. The CSV is the
input here, not an output. If you find yourself wanting to re-extract, the
tier call in Step 0 was wrong — re-check it rather than working around it.

## Step 2: Refresh the narrative

Spawn the `qualitative-analyst` agent (`.claude/agents/qualitative-analyst.md`)
with `run_in_background: false`. It should refresh, with emphasis on what
changes over weeks rather than years:

- **Recent developments** — the last 6–12 months, and specifically anything
  since the existing Analysis JSON's `analysis_date`
- Competitive position and moat, if either moved
- Key risks, bull case, bear case
- Any corporate action (takeover, raise, delisting, consolidation) — these
  invalidate a valuation outright and must be surfaced, not folded in

Write to `research/$ARGUMENTS/Reports/${ARGUMENTS}_Analysis.json`, preserving
the existing structure and setting a fresh `analysis_date`.

## Step 3: Decide whether the DCF assumptions actually moved

**This is the judgment step, and the reason this skill is cheap.**

The financials are unchanged by construction. So the DCF's inputs only move
if the *narrative* moved them. Compare the refreshed analysis against the
assumptions recorded in the existing `${ARGUMENTS}_DCF.json`:

- Growth rates, margin path, WACC, terminal growth, exit multiple, scenario
  weights, and the thesis each scenario rests on.

Then take one of two paths:

**(a) Nothing material changed** — the story is the same, the risks are the
same, no corporate action. Then the valuation still stands. Do **not** re-run
the DCF agent. Instead:

1. Refresh the price-derived numbers deterministically, for free:
   ```bash
   uv run python3 scripts/refresh_price.py --ticker $ARGUMENTS --apply
   ```
2. Update `valuation_date` in the DCF JSON to today, and add a short
   `refresh_note` recording that a tier-2 refresh confirmed the assumptions
   unchanged, with the date and what you checked.

`valuation_date` is the staleness key read by `refresh_plan`, `select_ticker`,
`filter_tickers` and `screen.py`. Setting it here is correct and deliberate:
a human-equivalent review did happen and found nothing to change. This is the
one place that is allowed — `refresh_price.py` must never write it, because a
price tick is not a review.

**(b) Something material changed** — new guidance, a thesis-breaking risk, a
corporate action, a competitive shift that moves growth or margins. Then spawn
the `dcf-analyst` agent (`.claude/agents/dcf-analyst.md`,
`run_in_background: false`) to rebuild the valuation from the **existing**
CSV plus the refreshed analysis.

State plainly which path you took and why. "Assumptions unchanged" is a
finding, not a shortcut — but it must be a conclusion you actually reached,
not a default. If you are unsure, take path (b): a re-run DCF is far cheaper
than a wrong valuation.

## Step 4: Regenerate the dashboard

Only if the CSV, Analysis or DCF changed. The dashboard embeds all three, so
a changed input means a stale dashboard.

Spawn `dashboard-generator` (`.claude/agents/dashboard-generator.md`), or —
if only `valuation_date` and price numbers moved under path (a) — note that
`refresh_price.py` already updated the dashboard's embedded copy and no
regeneration is needed.

**Verify before declaring done** (this agent has shipped broken JS before):

```bash
node --check <(python3 - <<'PY'
import re,sys
h=open("research/$ARGUMENTS/Reports/${ARGUMENTS}_Dashboard.html").read()
print("\n".join(re.findall(r"<script[^>]*>(.*?)</script>", h, re.S)))
PY
)
```

## Step 5: Canonical IV + index

```bash
uv run python3 scripts/canonical_iv.py --ticker $ARGUMENTS --apply
uv run python3 .claude/skills/screen-investments/screen.py --html "$(pwd)/index.html"
```

## Never end your turn with work still running

Same rule as `/research-stock`: spawn subagents with
`run_in_background: false` and wait. In a headless batch there is no later
turn for a backgrounded result to arrive in — several runs have exited
cleanly, reported success, cost $3.50–$4.91, and produced nothing.

## Done means

- [ ] Step 0 reported tier 2 (anything else: stopped and said so)
- [ ] `${ARGUMENTS}_Analysis.json` has a fresh `analysis_date`
- [ ] Path (a) or (b) chosen, stated, and justified
- [ ] `${ARGUMENTS}_DCF.json` has a current `valuation_date`
- [ ] Dashboard consistent with its three inputs
- [ ] **No** PDF downloaded, no text extracted, no facts rebuilt, no
      financial-parser run — if any of those happened, this was not a
      tier-2 refresh

Reply in at most two sentences: the ticker, the path taken, and the files
written.
