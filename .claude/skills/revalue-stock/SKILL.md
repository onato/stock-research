---
name: revalue-stock
description: Rebuild only the DCF valuation for a ticker whose metrics and analysis are current but whose DCF must be redone (e.g. it was built on the wrong model). Re-runs the dcf-analyst, then the sanity check, price, dashboard and eval gate. No download, parsing or qualitative stage.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
argument-hint: "[TICKER]"
---

# Revalue — $ARGUMENTS

The Metrics CSV and Analysis JSON are current and correct; only the valuation is
being redone. The usual reason: the DCF was built on a model other than the one
`.claude/agents/dcf-analyst.md` is pinned to. On 2026-09-25/26 the batch
orchestrator re-spawned the dcf-analyst on Sonnet when Fable was refused, and 89
tickers shipped Sonnet valuations (see `model-tiering-policy` in memory).

Do **not** run `/research-stock` or `/refresh-stock` steps: no download,
extraction, `financial-parser`, or `qualitative-analyst`. If the CSV or
Analysis JSON is missing, stop and say so — this is not a revalue ticker.

```bash
ls research/$ARGUMENTS/Reports/${ARGUMENTS}_Metrics.csv research/$ARGUMENTS/Reports/${ARGUMENTS}_Analysis.json
```

## Step 1: Rebuild the DCF

Spawn the `dcf-analyst` agent (`.claude/agents/dcf-analyst.md`,
`run_in_background: false`) to rebuild the valuation from scratch from the
existing CSV and Analysis JSON. Tell it the previous DCF (and `Drivers.json`, if
present) was built on the wrong model and must be re-judged, not copied —
it may read them for context only.

**If it returns "You've reached your Fable limit", do not re-spawn it and never
pass a `model` override.** Stop, leave the existing files alone, and say the
Fable limit was hit. The batch shell records the reset and re-runs the ticker
with Fable agents on opus.

## Step 2: Sanity check

```bash
uv run python3 scripts/sanity_check.py $ARGUMENTS --apply
```

Exit 3 means a trip rule fired: spawn the `dcf-analyst` **once more** with the
printed trip reasons and diagnosis candidates and the instruction to adjust the
drivers, rebuild, record `fix_applied` and `implied_multiples_after_fix`, and
stop. Do not compute multiples by hand.

## Step 3: Price

```bash
uv run python3 scripts/refresh_price.py --ticker $ARGUMENTS --apply
```

## Step 4: Dashboard, canonical IV, index

```bash
test -f research/$ARGUMENTS/Reports/${ARGUMENTS}_DashboardSpec.json || make dashboard-spec TICKER=$ARGUMENTS
make dashboard TICKER=$ARGUMENTS
uv run python3 scripts/canonical_iv.py --ticker $ARGUMENTS --apply
uv run python3 .claude/skills/screen-investments/screen.py --html "$(pwd)/index.html"
```

`FALLBACK` on the `slider engine:` line is a DCF-assumptions gap — send it back
to the dcf-analyst, never hand-edit the HTML.

## Exit gate

```bash
python3 scripts/run_evals.py --strict "$ARGUMENTS"
```

A non-zero exit means the run is not finished: fix what it names or say plainly
that the ticker is left broken. Never edit `tests/` or `scripts/` to pass it.

Spawn every subagent with `run_in_background: false` and wait — a headless batch
has no later turn for a backgrounded result to arrive in.

Reply in at most two sentences: the ticker, the old and new weighted IV, and
the files written.
