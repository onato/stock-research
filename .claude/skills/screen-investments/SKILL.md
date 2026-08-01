---
name: screen-investments
description: Ranks every ticker in the research folder by DCF upside (intrinsic value vs current price), refreshes prices live, flags stale/drifted data, and recommends the top N best risk-adjusted investments. Use when asked to compare the whole portfolio, find the most promising/undervalued names, or pick the top picks given current prices.
allowed-tools: Bash, Read, Glob, Agent
argument-hint: [N picks, default 3] [--max-upside | --quality]
---

# Investment Screen — Top Picks Across the Research Folder

Rank every ticker that has a `Reports/{TICKER}_DCF.json` by upside to its
probability-weighted intrinsic value, refresh prices so the ranking reflects
**today**, then apply a qualitative risk filter and recommend the top N.

Default N = 3. Default mode = **best risk-adjusted** (large upside AND a durable
business). Honor overrides in `$ARGUMENTS`: a number sets N; `--max-upside` ranks
purely on the gap; `--quality` favors well-known durable franchises.

This skill reads the data the `research-stock` skill produced. It does **not**
re-run DCFs. When a model is stale or built off a price far from today's, it warns
and points back to `research-stock` to refresh that ticker.

## Step 1: Run the screener (live prices)

The deterministic ranking lives in `screen.py` next to this file. Run it from the
research root with live prices so the upside reflects current quotes:

```bash
python3 .claude/skills/screen-investments/screen.py --live --top 15 \
  --json "$(pwd)/.claude/skills/screen-investments/last_run.json" \
  --html "$(pwd)/index.html"
```

What it does, per ticker `{T}/Reports/{T}_DCF.json`:
- upside = `probability_weighted.weighted_iv` / price − 1
- price = live Yahoo Finance quote (falls back to the DCF's stored `current_price` if the fetch fails)
- emits flags, never silently dropping anything:
  - `STALE(Nd)` — DCF/Analysis older than 45 days (tune with `--stale-days`)
  - `PRICE_DRIFT(N%)` — live price differs > 15% from the price the DCF was built on (tune with `--drift-pct`); the stored intrinsic value may no longer be internally consistent
  - `NO_IV` — DCF has no probability-weighted intrinsic value (e.g. unfinished model)
  - `NO_PRICE` — no usable price at all

If you have no network, drop `--live` to rank on stored prices (and say so in the
output — stored prices may be months old).

The full ranked list is written to `last_run.json` for the next steps, and the
repo-root `index.html` is regenerated — it IS the leaderboard (searchable,
sortable, every tracked company). Company names/sectors come from
`state/companies.json` (maintained by the research-stock skill). `index.html`
is generated output: never hand-edit it; re-running the screener refreshes it.
When presenting results (Step 4), mention that the full leaderboard is in
`index.html`.

## Step 2: Read the leaders' qualitative data

Take the top ~10 ranked names from Step 1 and read each one's
`Reports/{TICKER}_Analysis.json`, focusing on:
- `competitive_position.moat_factors` (and `moat_factors`) — is the moat real and wide?
- `bull_case` / `bear_case`
- `risks` / `key_risks` — especially anything tagged `"severity": "high"`

For a large leaders set, fan this out: launch a few **Agent** subagents (Explore
or general-purpose) in parallel, each reading 3–4 tickers' Analysis.json and
returning a 2-line quality verdict (moat strength + the single biggest risk). Keep
the conclusions, not the file dumps.

## Step 3: Apply the risk filter for the chosen mode

**Best risk-adjusted (default):** from the ranked leaders, keep names that have
BOTH large upside AND a durable, defensible business. Demote or exclude:
- value traps — structural decline dressed as cheapness (e.g. declining telecom, secular-loser retail)
- fragile micro-caps — heavy leverage, single-customer concentration, extreme cyclicality, thin liquidity
- unproven turnarounds — credibility-damaged names where the thesis rests on execution that hasn't shown up yet

A name with the highest raw upside but a disqualifying risk is an **honorable
mention**, not a top pick — name it and say why it was excluded.

**`--max-upside`:** skip the demotion; rank purely on upside. Still surface the flags.

**`--quality`:** bias toward large, well-known franchises trading below intrinsic
value even if a riskier name shows more upside.

Always exclude from the *top picks* (but list under "needs attention"):
- any `NO_IV` / `NO_PRICE` ticker (can't be compared)
- any `PRICE_DRIFT` ticker — its stored upside is unreliable; recommend re-running
  `research-stock {TICKER}` to rebuild the DCF at the current price before trusting it
- be explicit when a top pick still carries a `STALE` flag (the model is old even if
  the price barely moved)

## Step 4: Present the result

Output, concise:

1. **One-line setup** — N, mode, "live prices as of <date>" (or stored-price caveat).
2. **Top N table** — Ticker | live price | weighted IV | upside% | one-line thesis.
3. **Per pick (2–4 lines each):** why it qualifies (moat), the key bear-case risk to
   watch, and any `STALE` caveat.
4. **Honorable mentions** — higher-upside names excluded by the risk filter, each with
   the one-line reason.
5. **Needs attention** — `NO_IV` / `PRICE_DRIFT` / `NO_PRICE` tickers, with the
   `research-stock` refresh suggestion.
6. **Caveats** — rankings depend on each folder's own DCF assumptions (not market
   consensus); flag any STALE picks; close with "research synthesis, not financial advice."

## Notes

- Currency: the script reports each price in its native currency (NZD/EUR/USD). USD
  picks are directly comparable; treat cross-currency upside as approximate.
- `ASML`-style outliers (a stored price that looks 10×/100× off) usually mean a
  units/scale bug in that ticker's DCF — surface it under "needs attention," don't
  rank on it.
- To re-screen after refreshing a ticker, just re-run Step 1; it always reads the
  latest files on disk.
