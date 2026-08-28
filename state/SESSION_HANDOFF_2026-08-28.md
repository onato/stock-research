# Session handoff — 2026-08-28

Branch: `valuation-corrections-2026-08-28` (6 commits, working tree clean, NOT merged to main)

## What this session was about

Started as "compare the external WISE.L DCFs to ours". Became a systematic audit
after four consecutive models failed inspection. The recurring failure mode:
**a headline intrinsic value that doesn't survive checking its own inputs against
reported history.** Every number below was independently recomputed, not taken on
trust from the model that produced it.

## Portfolio (NZ$672,474 total, 2026-08-28)

| Ticker | Qty | Price | NZ$ | % | Status |
|---|---|---|---|---|---|
| FLOW.AS | 4,900 | EUR 29.06 | 278,608 | **41.4%** | up 138%; TRIM CANDIDATE |
| CASH | | | 182,543 | 27.1% | mostly USD |
| LBTYA | 3,114 | $10.54 | 55,097 | 8.2% | |
| 6995.T | 1,500 | ¥3,135 | 49,376 | 7.3% | already held |
| WISE.L | 1,800 | 980.2p | 40,279 | 6.0% | now ~12% overvalued |
| SUM.NZ | 3,859 | $8.50 | 33,689 | 5.0% | add candidate <$8 |
| SEK.NZ | 6,460 | $5.09 | 32,881 | 4.9% | |

NZ investor: **no capital gains tax on sale** (FIF/FDR regime — confirm FDR vs CV
with accountant). Selling costs spread + brokerage only, so the usual tax argument
for holding does not apply.

Portfolio file: `../portfolio-tracker/data/user_portfolio.json`
**Its WISE.L intrinsic_value field says 980 — stale. Our updated base is 861p.**

## Decisions reached

**Trim FLOW.AS from 41.4% to ~20-25%; hold proceeds in cash.**
Not because FLOW is bad — it's cheap at 7.5-8.3x normalized EPS and 1.32x liquid
book — but because ~+17% upside doesn't justify 41% of the book in a business whose
EPS ranged EUR 0.84-10.39 over 11 years, pays nothing until 2030, and sits at the
top of its P/B regime. Searched the whole research folder; there is no better idea
right now, and cash at 27%+ directly serves the user's AI-bubble concern.

**SUM.NZ under $8 is the one genuine buy idea.** Base IV $12.91, entry price
$11.06, currently $8.50. Use a limit order — the stock just rose 9% on a number
that went backwards.

## Corrections made (all committed)

- **WISE.L** — terminal EBITDA margin 36% -> 30%, flattened from Y7. Base
  $13.50 -> $11.70, weighted $13.93 -> $12.20. With the price move (885.6p ->
  980.2p) it goes from +13% upside to ~12% overvalued. WACC unchanged at 11.5%.
- **FLOW.AS** — headline EUR 38.29 contradicted its own commentary (EUR 27-28 =
  the 14-16% CoE sensitivity rows). Three input errors: declining variable-comp
  share (contractual, can't shrink), stale NTI base (FY2026E 519.8 vs H1
  annualised 607.4), over-severe bear. Rebuilt weighted 35.74; **defensible range
  EUR 30-40, central ~34**.
- **6995.T** — sanity_check claimed uncapped Gordon = ">6x EBITDA, IV ~JPY6,800".
  Actually 4.74x and JPY4,253. TSE governance catalyst is worth ~6pp of upside,
  not a windfall.
- **PINS** — dashboard panel added. Entire upside is an assumed SBC decline from
  19.5% to 7.0% of revenue; six years of actuals sit in a 16-21% band with no
  downtrend. Flat SBC => $16.85 (-26%) vs model $33.69 (+47%).
- **SUM.NZ** — refreshed on H1-2026. Weighted 16.81 -> 12.90.
- **IFT.NZ** — refreshed. Weighted 17.47 -> 15.35, upside 20.3% -> 6.4%. OUT.

## Candidates screened and rejected

| Ticker | Why out |
|---|---|
| PYPL | Declining business, no turnaround evidence (user's call) |
| DCBO | Acquisition-polluted numbers (user's call) |
| PINS | Upside is entirely an unevidenced SBC decline |
| IFT.NZ | +6.4% after refresh; bear -50% |

## NEXT TASK: look at APP (AppLovin)

The user asked for this and we ran out of context before starting.

**Why:** APP is $312.63, **down 49% from its 6-month high of $613.70**, 52-week
range $297.50-$745.61 — near the low. Existing research is **65 days stale**
(flagged by `make screen`), stored upside +17.8%, which is unreliable at that age
after a move this size.

**Suggested approach:**
1. `research/APP/Reports/APP_DCF.json` — read first. Check the valuation date and
   what price it was built on.
2. Establish what actually caused the ~50% drawdown — this is the whole question.
   Guidance cut? Multiple compression? Sector rotation? Short report? The recent
   10-Q and any 8-K are the place to look.
3. Given this session's hit rate, **check the model's load-bearing assumption
   against reported history before trusting the IV.** For a company like APP the
   candidates are: SBC treatment, growth-rate extrapolation off a hypergrowth
   base, and terminal multiple vs traded history.
4. APP is AI/adtech-adjacent. The user is explicitly avoiding correlated AI
   exposure (sold BABA Jan-2026 for that reason) — factor that into any
   recommendation, don't just report the upside number.

## Method notes that earned their keep

- **Recompute every headline independently** before relying on it. Four of four
  models this session had errors their own files contained enough information to
  expose.
- **Check the model's own warning fields** — SUM's `growth_divergence_warning`
  correctly flagged the error that made its IV 30% too high, and had simply not
  been acted on.
- Reproduce external workbooks exactly before critiquing them (Carlin's FLOW tab
  reproduced to the cent), then test their *inputs* against filed history.
- LibreOffice `--convert-to` does not reliably recalculate; reimplementing a
  model in Python is faster and gives control to test variants.
- Chrome extension **cannot load `file://` URLs** — verify dashboards by parsing
  the DOM and extracting visible text instead.
- NZX filings: `state/` memory note `nzx-ssr-json-endpoint` works; buildId must be
  scraped fresh each time.

## Open items

- **Fable 5 was rate-limited all session.** WISE and SUM refreshes ran on Opus;
  both flagged in provenance for Fable review when quota returns. Per the
  model-tiering policy, valuation is Fable's tier.
- **SUM.NZ H1-2026 is not in `core_metrics`/Metrics.csv.** Needs the
  financial-parser agent. The CSV must never be hand-written.
- **FLOW.AS and PINS headline IVs left as published** (38.29 and 31.26) with
  caveats attached rather than overwritten — `make screen` still ranks them on the
  old numbers. Deliberate, but worth revisiting if the user wants the screen to
  reflect the corrections.
- FLOW's FY2026 results carry the first Horizon 2030 progress report — the first
  real evidence on whether the expanded trading capital earns its cost.
- Branch is not merged. Merge or PR when ready.
