# Owner-FCF DCF (operating companies)

The house-standard valuation engine: a three-scenario, probability-weighted DCF on a
strict owner-FCF basis, with dual house/street reporting, hurdle-rate entry prices and
a formula-driven workbook.

**Use this method for** operating companies whose value is a stream of free cash flow —
SaaS, consumer internet and marketplaces, and equally non-tech and non-US operating
companies: industrials, consumer names, cyclicals, Japanese keiretsu suppliers. The
engine is currency-aware; section 7 carries the adaptations for leases, minority
interests, non-operating assets and SBC-zero markets. **Read section 7 before any
non-US or cyclical build** — each item there encodes a real error.

**Do NOT use it for** banks, insurers, REITs, LICs, holdcos, investment trusts or
distressed shells. Their cash flow statements do not mean what this engine assumes —
a bank's deposits are not net debt, a REIT's AFFO is already post-interest, an
insurer's OCF is noise. `../SKILL.md` routes those to the right method; if you arrived
here for one of them, stop and re-read the routing table.


## 1. Owner FCF: the SBC, interest and tax adjustments

SBC is a real economic cost to shareholders. Charge it **exactly once** — expense it in the cash flows, and hold the share count flat:

```
owner_fcf = reported_fcf − sbc_incl_equity_taxes − interest_income
projected_shares[t] = shares_outstanding        # FLAT. See "charge it once" below.
annual_share_growth_pct = 0.0
```

**Charge it once: expense SBC OR dilute the count — never both.** Fully expensing SBC
in the flows *is* the charge for grant dilution; it is what the company would have had
to pay in cash to keep the count flat. Deducting SBC from FCF *and* compounding the
share count charges shareholders the same cost twice. Over a 10-year horizon a 2%/yr
share-growth assumption on top of a full SBC deduction understates value by ~11%.

Buybacks are therefore **value-neutral** in this model: they are the cash mechanism by
which the count stays flat, and that cash is already deducted as SBC. Still record
`buyback_cash` — it tells the reader whether the flat count is being funded (repurchaser)
or is a modelling convention the company has not yet paid for (non-repurchaser like
DUOL). State which in `inputs.notes`.

The one case for a rising count is genuine **non-SBC** issuance — an equity raise,
acquisition currency, or convertible conversion. That is not compensation, is not in
the SBC line, and must be modelled explicitly with its own note.

`owner_fcf` is what goes into `last_fcf`. Each of the three terms matters:

**(a) SBC must include cash taxes paid on equity awards.** The cash-flow-statement SBC add-back understates the true cost, because companies also spend real cash withholding taxes on net-share settlement — an outflow that appears in *financing* activities and is easy to mistake for a buyback.

Prefer the company's **own combined figure from the adjusted-EBITDA reconciliation** (typically labelled `Stock-based compensation expenses related to equity awards`). For DUOL FY2025 that is **$148.6M**, versus a $137.4M cash-flow add-back. Do NOT add the $41.6M of equity-award taxes on top of $137.4M — the $179.0M that produces double-counts, because the reconciliation line already absorbs part of the tax cost. Only sum the two lines when the filer publishes no combined figure, and record which basis you used in `sbc_source`.

**(b) Strip interest income when the company holds significant net cash.** If you add net cash to enterprise value at the end of the DCF *and* leave the interest that cash earns inside FCF, you have counted the cash pile twice. Deduct interest income from the FCF base whenever net cash is material (rule of thumb: net cash > 1× the FCF base — this is common, roughly 26 tickers in this folder). For DUOL, ~$45.2M/yr on a $1.14B pile is worth ~$23/share of phantom value. Interest income is on the income statement; do not confuse it with interest *expense* on debt.

**(c) Project SBC as a % of revenue, never as a flat dollar amount.** Holding SBC constant in dollars while revenue compounds is not conservative — it silently assumes SBC falls as a share of revenue without management ever committing to that. DUOL's own guidance frames SBC as ~15% of revenue.

```
sbc[t] = revenue[t] × sbc_pct[t]      # sbc_pct declines gradually, e.g. 15% -> 10% over 10yr
```
Anchor `sbc_pct[0]` to the latest actual (SBC ÷ revenue) and let it decline only as fast as the business genuinely matures — 0.5pp/yr is a reasonable default. Over a 10-year build this charges materially more than a flat dollar figure (for DUOL, ~$933M more) and is the single most consequential of these three corrections.

Apply all three adjustments across the whole FCF history so the historical CAGRs (computed by the agent) are derived from a consistent owner-FCF series.

### Dual basis: house and street

Report the valuation on **two bases**, always, side by side:

- **House basis** (the headline, and what `intrinsic_value` means): SBC fully expensed, as above.
- **Street basis** (memo): SBC treated as a non-cash add-back, the convention sell-side
  and the company's own "adjusted" figures use. Compute it by adding back the PV of the
  projected SBC charge — both the explicit years and its terminal value:

```
street_equity  = equity_value
               + Σ_t  sbc[t] / (1+wacc)^t                      # explicit years
               + (sbc[N] × (1+g_term) / (wacc − g_term)) / (1+wacc)^N   # terminal
street_iv      = street_equity / shares_outstanding
```

The two are not competing estimates — they are the **accounting-regime band** around the
price. Where the price sits inside that band is itself the finding:

```
band_position = (current_price − house_iv) / (street_iv − house_iv)
```

0% means the market is paying the fully-SBC-expensed price; 100% means it is paying the
street price and giving the company full credit for treating comp as non-cash. Report
`band_position` in the JSON and say plainly which regime the current price implies. The
spread between the two is the honest measure of how much the valuation depends on SBC
treatment — for a heavy issuer it is most of the market cap.

Report all three figures so the reader can see the gaps: `reported_fcf` (company
definition), `last_fcf` (owner FCF, house), and `street_iv` per scenario.

## 2. Build the forward FCF margin from components

**Do NOT carry the latest year's FCF margin forward as a constant.** The most recent margin usually embeds one-offs that will not repeat, and a flat margin silently assumes they do. Build each projected year from components instead:

```
adj_ebitda[t]   = revenue[t] × ebitda_margin[t]      # margin ramps toward a stated maturity level
− sbc[t]        = revenue[t] × sbc_pct[t]            # declining % of revenue (section 1)
= owner_ebitda[t]
− d_and_a[t]    = revenue[t] × da_pct
= ebit[t]
× (1 − cash_tax_rate[t])                             # RAMP to a normal rate — see below
= nopat[t]
+ d_and_a[t]                                         # non-cash, add back
− capex[t]      = revenue[t] × capex_pct
+ working_capital[t] = wc_capture × (revenue[t] − revenue[t−1])   # NOTE: on the CHANGE
= owner_fcf[t]
```

Three traps this exists to catch:

**(a) Working capital scales with GROWTH, not with revenue.** For subscription and prepaid businesses the deferred-revenue increase is a real cash inflow sitting inside OCF — but it is a function of *bookings growth*. Model it as a capture rate on the **YoY revenue increase** (`wc_capture` ≈ 15-20% is typical), never as a fixed share of revenue. When growth decelerates the tailwind shrinks hard, and a flat-margin model completely misses that.

For DUOL FY2025 the deferred-revenue increase was $123.3M — **34% of the entire $360.4M reported FCF**. Strip it and the underlying owner-FCF margin was 4.2%, not 16.1%. Carrying the FY2025 margin forward assumed that windfall repeats every year in perpetuity.

**(b) Cash tax rates must ramp to normal.** A year with a valuation-allowance release, one-off credit or loss carryforward is not a repeatable base. Check whether the latest effective cash tax rate is abnormal; if so, ramp it to the statutory rate over 3-5 years and record the path. DUOL guided a **−6% benefit** for 2026 (post valuation-allowance release) heading to ~21% — assuming the benefit persists is a large silent overstatement.

**(c) Sanity-check the implied margin against the components.** After building the path, print `owner_fcf[t] / revenue[t]` for each year and compare with the latest actual. If the projected first-year margin is far above the component-built figure, something is being carried forward that shouldn't be. Record the path in `projections[scenario].owner_fcf_margin`.

**(d) Extend the horizon until the margin has matured before capitalizing.** The component build and a 5-year horizon interact badly: if margins are still ramping in the terminal year, Gordon growth capitalizes an immature margin in perpetuity and systematically undervalues the business.

**Use a 10-year explicit build whenever the year-5 owner-FCF margin is still materially below its projected mature level.** For DUOL the difference is stark — the same assumptions give **$76.21 on 5 years** (terminal margin 13.2%, still climbing) versus **$87.96 on 10 years** (terminal margin 16.4%, matured). Capitalizing the 13.2% understates the company by ~15%.

A 5-year horizon remains fine for businesses already at steady-state margins. The test is whether the terminal-year margin is roughly flat, not the number of years.

Anchor each component to the latest actuals and to management guidance where it exists; let margins improve only as fast as the business genuinely matures. Record the component assumptions in `assumptions[scenario]` so a reader can check each line independently rather than having to accept one blended margin number.

Read from `./research/{ticker}/Reports/{TICKER}_Analysis.json`:
- Business model summary
- Growth drivers
- Risk factors
- Bull/bear case points


## 3. Scenarios

### Base Case
- Growth: Historical equity CAGR, decelerating toward terminal growth
- Margins: Slight improvement or stable based on maturity
- FCF conversion: Historical average

### Bull Case
- Growth: +5-10pp above base in early years
- Assumptions align with bull case from analysis JSON
- Success scenarios for growth drivers

### Bear Case
- Growth: -5-10pp below base
- Assumptions align with bear case from analysis JSON
- Risk factors materialize

### Scenario narratives and weights

Every scenario needs a **one-paragraph narrative naming this company's actual bull and
bear axes** — not generic "growth disappoints". The narrative is what makes the driver
path auditable: a reader should be able to check whether the numbers follow from the
story. For PINS the axes are ad-market cyclicality, engagement trends, the ARPU gap to
peers, and AI ad-tech gains versus AI answer-engines eroding discovery traffic. For DUOL
they are DAU-to-bookings conversion and whether AI assistants absorb casual practice.
Write the equivalent for this company, drawn from the Analysis JSON's bull/bear points.

Probability weights **default to 15% bear / 50% base / 35% bull** and are a judgment
call you must actually make, not a constant to copy. Move them, and say why in
`probability_weighted.weights_rationale`, when the situation warrants:

- Bear weight up (25-35%+) for going-concern risk, covenant pressure, a single-customer
  or single-regulator dependency, or a thesis resting on an unproven pivot.
- Bull weight down when the bull case needs several independent things to go right.
- A live corporate action (takeover, scheme, strategic review) usually breaks the
  three-scenario frame entirely — weight deal-completes vs deal-breaks instead, and
  say so.

Weights must sum to 1.0. State the rationale in one sentence per scenario.

## 4. DCF calculation

For each scenario (Base/Bull/Bear):
1. Project FCF over the horizon `N` chosen in section 2 (5 years if margins are already mature, 10 if still ramping), starting from **owner FCF** (section 1). Subtract the forward SBC charge as a % of revenue each year — do not carry a flat dollar SBC.
2. Calculate Terminal Value as the **lower** of:
   - Gordon: `FCF_year5 * (1 + terminal_growth) / (WACC - terminal_growth)`
   - Cap: `20 × FCF_year5` (a multiple the market plausibly pays for a mature compounder)

   **and floored at zero**: `TV = max(0, min(gordon, cap))`.

   Record which one bound. When Gordon exceeds the cap, the model was assuming an exit multiple no buyer has to grant; the cap is what keeps a small change in `terminal_growth` from swinging the valuation wildly. Use a lower cap (12-15×) for cyclical or structurally slower businesses.

   **The floor is not cosmetic.** When terminal-year FCF is negative, `MIN` selects the *more negative* of two negative numbers — the formula silently picks the harsher answer and the valuation runs away downward. A real owner facing a perpetually cash-burning operation shuts or sells it; zero is the right floor. **Flag in the report any scenario that hits it** — a bear case sitting on the floor is telling you the business does not survive that path, which is a finding, not a rounding detail.
3. Discount all cash flows to present value
4. Sum PV of FCF + PV of Terminal Value = Enterprise Value
5. Subtract Net Debt (Total Debt - Cash)
6. Divide by **`shares_outstanding`** (the current fully diluted count) = Intrinsic Value per share

Use the **fully diluted** count from the latest filing's dilutive-securities table — not
basic shares, and not a stale cover-page number. The count is held flat (section 1), so
`projected_shares` is a flat array and the divisor is the same in every year; record
`horizon_years: N` in the JSON so downstream consumers discount consistently.

Discounting is **end-of-year**: year 1 is discounted one full period. Do not use
mid-year convention here — it is not what the rest of this folder's models assume, and
mixing the two makes valuations non-comparable across tickers.

### Entry Price Calculation
The entry price is the price at which buying today, collecting the projected interim FCF, and exiting in the terminal year `N` earns a **15% IRR** (`N` is the horizon chosen in section 2 — normally 10):

`Entry Price = ( Σ FCF_t / 1.15^t  +  TV_N@hurdle / 1.15^N  −  net_debt ) / shares_outstanding`  (t = 1..N)

- `FCF_t` = the base scenario's projected **SBC-adjusted** FCFs (the decelerating path, not a constant growth rate)
- `shares_outstanding` = the flat fully diluted count, matching the intrinsic value calculation
- `TV_N@hurdle` = `min( FCF_N × (1 + terminal_growth) / (hurdle − terminal_growth), cap × FCF_N )` — **built at the hurdle rate, not at WACC**, and re-capped at that rate
- `− net_debt` adds net cash (net_debt is negative for net-cash companies, so subtracting it increases the entry price)

**Build the terminal value at the same rate you discount at.** It is tempting to argue
that `TV_N` should keep the WACC-based Gordon value "because that is the fair price a
buyer pays at exit" — this folder's spec said exactly that until 2026-09-01, and it is
the single biggest error the method has produced (34 tickers, entry prices overstated a
median 28%; SAN.PA base €59.17 vs €44.48). Both versions do arithmetically return 15%,
so nothing looks broken. The reason the WACC version is wrong as a default:

- It silently assumes you exit to a buyer who accepts the WACC return while you demanded
  15%. That is not a hurdle rate; it is a bet on finding a cheaper-capital buyer, and it
  is precisely the multiple-expansion assumption the `implied_exit_multiple` disclosure
  in section 4 exists to flag. Charging a hurdle on the flows and not on the exit is the
  same double-standard as charging risk twice (section 7f), pointed the other way.
- **The cap makes it worse, not safer.** The cap is a brake on Gordon runaway at low
  discount rates. At a 15% hurdle Gordon is already modest (SAN.PA: 7.5x), so the brake
  is irrelevant — but carrying the WACC-built number across imports a terminal multiple
  chosen for a *different* discount rate. On SAN.PA the entry price inherited 15x against
  a self-consistent 7.5x: the cap doubled the terminal value at the hurdle rate.

If a specific ticker genuinely has a credible lower-cost exit buyer (an announced trade
sale, a binding scheme), model that as a scenario with its own exit multiple and say so
in `entry_price.method` — do not bake it into the house default.

Do NOT compute entry price as terminal value per share discounted at 15% alone — that ignores the interim FCF and net cash and produces an entry price far too low.

**Sanity property.** For a slow-growing business a correct hurdle entry price commonly
lands near *half* of intrinsic value, and that is not a bug — it is what demanding 15%
from an asset the model discounts at 7-9% costs. (The pre-2026-09-01 spec asserted the
opposite here, "never at ~half of it", which is what made the overstated numbers look
right.) The real check is self-consistency: recompute the IRR implied by your entry
price using a terminal value built at that same IRR, and confirm it returns the hurdle.

Compute the hurdle entry price for **all three scenarios**, then probability-weight them
the same way as intrinsic value. The weighted figure is the model's buy-below price and
is what the reader acts on; a base-only entry price silently assumes the base case is
certain. Report the per-scenario spread too — when the bear-case entry price is far
below the current price, the 15% hurdle is only met if the bear case is avoided.

### Required-return table

Alongside the single 15% number, report base-case value per share across a range of
required returns — **9%, 10%, 11%, 12%, 15%** plus the model's own WACC. This answers
the question the reader actually has ("what return does today's price imply?") far
better than one hurdle does, and it exposes how steeply value falls with the discount
rate for a long-duration, terminal-heavy business. Emit it as `required_return_table`.

**Every row rebuilds its own terminal value at that row's rate**, exactly as the entry
price does — `min(Gordon@r, cap × FCF_N)`, re-capped at `r`. A table that holds TV at the
WACC-built value and varies only the discounting is not a required-return table: it
understates how fast value falls and it makes the "price implies an X% IRR" reading too
generous. Cross-check: the 15% row must equal the base-case entry price, and the WACC row
must equal `valuation.base.intrinsic_value`. If either fails, the table and the entry
price disagree about the model.

**Non-FCF valuation models** (BVPS-compounding insurers/holdcos, residual-income banks): the same principle applies — entry price = the price at which interim distributions plus the year-5 exit value deliver a 15% IRR. The build-TV-at-the-hurdle rule carries over: if the exit value is an exit multiple (P/B, P/E) rather than a Gordon value, that multiple must be the one a 15%-hurdle buyer would pay, not the base-case multiple reused. For dividend payers, add the PV (at 15%) of projected dividends to the discounted exit value. For book-value models, retained earnings are already inside the projected year-5 BVPS/exit value, so do NOT add interim earnings back — only add distributions (dividends/buyback proceeds) the exit value doesn't capture.

### Valuation Disclosures
Report per scenario alongside the valuation:
- `terminal_pct_of_value` = PV(terminal) / Enterprise Value × 100 — flags terminal-value-dominated valuations
- `implied_exit_multiple` = TV_N / FCF_N — compare against the current EV/FCF multiple and flag when the implied exit multiple exceeds today's (the model is then assuming multiple expansion)

## 5. Sensitivity analysis

Create matrix of prices across:
- WACC: Default +/- 2% (e.g., 8%, 9%, 10%, 11%, 12%)
- Terminal Growth: Default +/- 1% (e.g., 2%, 2.5%, 3%, 3.5%, 4%)


## 6. Excel workbook

**Generated, not written.** `make build-dcf TICKER={TICKER}` emits
`{TICKER}_DCF_Model.xlsx` from the same Drivers file that produced the JSON, so the
two cannot disagree. Do not write an openpyxl generator, and do not paste values over
the workbook: edit the Drivers file and rebuild.

What the generated workbook contains, for the reader:

- **`Model_Info`** — provenance: ticker, dates, price, currency, engine version, the
  Drivers file it was built from.
- **`Reconciliation`** (updates only) — prior vs new per-share values and the bridge.
- **`Actuals`** — the annual rows of the Metrics CSV.
- **`Assumptions`** — every driver path per scenario (blue = editable input): growth,
  EBITDA margin, SBC %, lease %, D&A %, capex %, cash tax; WACC, terminal growth, cap
  multiple, working-capital capture, weight. Plus price, final share count, net debt,
  equity bridge, hurdle rate, base revenue, FX.
- **`DCF_Bear` / `DCF_Base` / `DCF_Bull`** — the section-2 component build as live
  formulas linked to `Assumptions`: revenue → EBITDA → SBC and lease charges → D&A →
  EBIT → cash tax (never credited on a loss) → NOPAT → D&A add-back → capex → working
  capital → unlevered owner FCF → discount factors → PV; then the section-4 valuation
  block (`TV = MAX(0, MIN(Gordon, cap x FCF_N))`, EV, bridge, equity, value per share
  at `$B$38`), the street memo (`$B$43`), and the hurdle entry price with its own
  terminal value rebuilt at the hurdle (`$B$47`).
- **`Valuation`** — the scenario table, `SUMPRODUCT` weighting, street weighted value,
  band position, weighted entry price, the required-return table and the WACC x g grid,
  each cell in closed form over the base-case FCF row.

Every driver edit on `Assumptions` flows through to every value. The build recalculates
the workbook headlessly in its own tests (LibreOffice, zero formula errors, scenario
values tie to the JSON), so you do not run a recalc yourself.

## 7. Non-US and cyclical adaptations

The engine above was built on US tech. It transfers to industrials, cyclicals and
non-US filers, but six things change. Each one below is a real error that was made and
caught — the first three in the Toyota Boshoku and Tokai Rika work of 14-Aug-2026, where
a third-party model and the house model got them wrong independently.

**(a) Currency.** Two different problems live here. Solve both, and record both.

**(a1) Discount rate currency.** Build WACC from the **same currency's** risk-free rate
and equity risk premium as the flows. Never discount JPY flows at a USD-built rate.
House reference: ~9% for JPY keiretsu industrials against the 10.5% USD tech default.
The sensitivity grid's terminal-growth columns should derive from the base scenario's
own terminal growth (±1pp in 0.5pp steps, clipped at zero), not a hardcoded 2-4% band
that may not even bracket the case being modelled.

**(a2) Reporting currency ≠ quote currency.** This is the more dangerous one, and it is
not rare: **16 of ~150 tickers in this folder report in a currency they are not quoted
in.** An NZX listing is not an NZD filer — SMI.NZ and MKR.NZ report AUD, ANZ.NZ and
EBO.NZ report AUD, ARB.NZ reports USD; WISE.L files USD and quotes GBp; 0285.HK,
9626.HK and 9999.HK report RMB against an HKD quote; KKS.F reports KZT against a EUR
quote. **Never infer the reporting currency from the ticker suffix.** Read it off the
statements.

Three fields, always, even when they agree:

```
inputs.currency        # ISO-4217 of the FLOWS and the statements: "AUD"
inputs.quote_currency  # ISO-4217 of the market PRICE: "NZD" (GBp for pence quotes)
inputs.fx_note         # the rate, its date, its source, and the parity check
```

`inputs.currency` is an **ISO code and nothing else** — not "NZ$", not a prose sentence
like "AUD (fundamentals) / NZD (outputs)". Where the two bases genuinely differ, that is
what `quote_currency` is for; explain the split in `fx_note`, not in the code field.

Rules that follow:

- **`current_price` and every `intrinsic_value` must be denominated the same way.**
  This is the one an owner acts on. A price in a different currency from the IV divides
  to a plausible-looking upside that is pure noise — WISE.L's 885.6 / 48.43 gives a P/E
  of 18.3 by coincidence, which is why `screen_fundamentals` refuses such rows with
  `price-currency-mismatch`.
- **Emit a currency-suffixed twin for the non-canonical basis** —
  `intrinsic_value_gbp_pence`, `weighted_iv_gbp_pence`, `entry_price_nzd`. Keep the
  unsuffixed key in the **quote** currency so a naive reader of
  `weighted_iv` vs `current_price` cannot get a wrong answer. `scripts/canonical_iv.py`
  (`make canonical-iv`) selects by observed quote currency and will flag a file whose
  canonical key is in the wrong one.
- **Pence, not pounds.** A GBp quote is 1/100 GBP. Say `GBp` in `quote_currency` — never
  `GBP` — and check the magnitude: an IV two orders of magnitude off the price is this
  bug, not a valuation view.
- **Parity-check a dual listing when one exists**, and put it in `fx_note`. SMI.NZ is
  the worked example: `A$0.51 × 1.2123 = NZ$0.618` against an observed NZ$0.615, a 0.5%
  tie that proves the rate, the direction and the quote basis in one line. Getting the
  direction wrong is the classic error and a parity check catches it instantly.
- **Date the rate and name the source.** An undated FX rate cannot be re-derived later;
  `refresh_price.py` moves the price but never the rate, so a stale rate silently rots.

> Worked example — **SMI.NZ**: statements and PFS in AUD, NZX quote in NZD. All
> per-share values computed in AUD then converted at AUD/NZD 1.2123 (Yahoo AUDNZD=X,
> 2026-09-01), with the SMI.AX cross-listing used as the parity check. That file is the
> template for this section.

**(b) Leases — pick ONE treatment and say which.** Under IFRS 16, lease principal sits
in *financing*, so reported OCF and FCF are flattered. Two valid treatments:

- **(A) Leases as debt** — do NOT deduct principal from the flows; subtract lease
  liabilities once in the net-debt bridge; charge ongoing lease-asset renewal through a
  capex-like line.
- **(B) Leases as operating cost** — deduct the full lease payment (principal **and**
  interest, both on the cash-flow statement) inside adjusted EBITDA and **exclude lease
  liabilities from net debt**. Cash-tax EBIT must then use D&A ex right-of-use
  depreciation, or the lease is charged twice.

Charging **both** (a third-party Boshoku model) cost ~¥800-1,000/share. Charging
roughly **neither** inflates value by a similar order. Check the reporting standard
first: J-GAAP filers mostly expense operating leases already and need no adjustment.
State the choice in `inputs.notes` and in the workbook's Model_Info.

> This folder's 3116.T uses treatment (B): ¥42,784m FY2026 lease principal deducted from
> flows, and `total_debt` of ¥189,335m is bonds and borrowings only (34,335 current +
> 155,000 non-current) with lease liabilities excluded. That is internally consistent.
> Before changing a lease treatment on an existing ticker, verify which one it already
> uses — switching halfway is how the double-charge appears.

**(c) Minority interests — charge once.** Either make the flows attributable-only (fold
the NCI profit share into a combined tax/leakage driver) **or** deduct the NCI stock in
the bridge. Never both — that was ~¥245/share on Boshoku, which runs ~24-26% NCI.
Calibrate: modelled FY0 NOPAT must tie to reported **attributable** net income within
non-operating noise. If it doesn't, the leakage rate is wrong.

**(d) Excluded income needs its matching asset.** This is the interest-income rule of
section 1, generalized: **every income stream stripped from the flows means its source
asset goes into the bridge at value, and every asset added to the bridge means its
income leaves the flows.** Stripping dividend income while omitting the
cross-shareholding and investment-securities portfolio deleted ~¥390+/share of real
value on Boshoku — and both the house and third-party models missed it independently.
For Japanese filers take these at fair value from the yuho, not just cash, and itemize
the composite in the net-cash note.

**(e) SBC-zero markets.** Many non-US filers have no material share-based payment. Keep
the explicit SBC rows at 0% rather than deleting them — the house rule is that SBC is
always visible as its own line. The house/street band then collapses to a point; show
the band cell as `n/a` and say so in the report rather than reporting a degenerate 0%.
Set `band_position: null` in the JSON when house and street coincide.

**(f) Charge risk once.** Probability-weighted scenarios already price cyclicality,
customer concentration and governance risk **in the flows**. Stacking an ad-hoc WACC
premium on top charges it twice; using a higher WACC on the bear scenario than the bull
charges it a third time. Risk lives in the flows **or** in the discount rate — decide
which, once, and write the decision down.

### Optional: the no-re-rate reference value

For a chronically low-multiple name — one that has never traded above, say, ~3x
EV/EBITDA in a decade — report a supplementary value with the terminal value capped at
the stock's **own** historical multiple range, alongside the house Gordon value. The gap
between the two is the price of the re-rating thesis, stated explicitly instead of
buried in a terminal assumption.

Report it as a **memo, not a replacement** for the house basis. 3116.T is the live
example here: hist EV/EBITDA of 2.29-3.22x against a Gordon-based terminal value is
precisely the case this is for.
## 8. Quality checklist

Before finishing, verify every box. The items are grouped by the error each one catches.

**Owner-FCF basis**
- [ ] `last_fcf == reported_fcf - sbc_incl_equity_taxes - interest_income`, with `sbc_source` cited (or `"unavailable"` plus a warning banner if genuinely undisclosed)
- [ ] **SBC includes cash taxes on equity awards**, not just the cash-flow add-back — and is the company's own combined figure where it publishes one, not the two lines summed
- [ ] **Interest income stripped** whenever net cash > 1x the FCF base — otherwise the cash pile is counted twice (once in FCF, once added to EV)
- [ ] **All three adjustments applied to the whole history**, so FCF CAGRs are computed on a consistent owner-FCF series
- [ ] **Units**: SBC converted from filing units (usually thousands) to the CSV's millions

**Charge each cost exactly once**
- [ ] **SBC expensed AND share count held flat** — never both expensed and diluted. `annual_share_growth_pct` is 0 unless there is genuine non-SBC issuance, and `share_count_basis` says which
- [ ] Share count is the **fully diluted** figure from the latest dilutive-securities table, not basic shares or a stale cover page
- [ ] **Buyback sanity**: `buyback_cash` reflects actual financing-activity outflow, not an announced authorization

**Forward build**
- [ ] **Forward SBC projected as % of revenue**, declining gradually — never held flat in dollars while revenue compounds
- [ ] **FCF margin built from components** (EBITDA -> SBC -> D&A -> tax -> capex -> working capital), NOT carried forward flat from the latest year
- [ ] **Working capital modelled on the CHANGE in revenue**, not as a share of revenue — deferred-revenue tailwinds shrink when growth decelerates, and the sign matches the business model (prepaid subscription positive, ad-supported ~zero or negative)
- [ ] **Cash tax rate ramps to normal** if the latest year had a valuation-allowance release, one-off credit, or carryforward benefit — model the CASH rate, which can differ from the GAAP provision
- [ ] **Projected year-1 owner-FCF margin sanity-checked** against the component build — if it is far above, an unrepeatable one-off is being carried forward
- [ ] **Horizon runs until the margin matures** — 10 years unless the year-5 owner-FCF margin is already flat

**Scenarios and outputs**
- [ ] Bear/Base/Bull have distinct driver paths and a **narrative naming this company's actual axes**, not generic optimism and pessimism
- [ ] Weights are a stated judgment with `weights_rationale`, summing to 1.0 — not a copied default
- [ ] Terminal value is `max(0, min(Gordon, cap))`; which one bound is recorded, and any scenario hitting the zero floor is flagged in the report
- [ ] Net debt calculated correctly (Debt - Cash)
- [ ] `terminal_pct_of_value` and `implied_exit_multiple` reported per scenario; flagged if the exit multiple exceeds today's EV/FCF
- [ ] **Dual basis**: `street_intrinsic_value` per scenario, `street_weighted_iv`, and `band_position` all present
- [ ] Entry price includes PV of interim FCF at 15%, PV of terminal value at 15%, and the net-debt adjustment, per share — not terminal value alone; computed for **all three scenarios** and probability-weighted; it sits below IV but not dramatically below when growth ~ hurdle
- [ ] `required_return_table` emitted
- [ ] Sensitivity matrix covers WACC +/-2% and terminal growth +/-1%

**Non-US / cyclical builds (section 7)**
- [ ] WACC built from the **flows' own currency** risk-free rate and ERP
- [ ] `inputs.currency` **and** `inputs.quote_currency` both present as bare ISO codes (`GBp` for pence), even when identical — never inferred from the ticker suffix
- [ ] Where they differ: an `fx_note` giving the rate, its date, its source and a parity check; a currency-suffixed twin for the non-canonical basis; and the unsuffixed `intrinsic_value`/`weighted_iv` left in the **quote** currency so they are comparable to `current_price`
- [ ] **One** lease treatment chosen, stated, and consistent — principal deducted from flows XOR lease liabilities in net debt, never both and never neither
- [ ] Under treatment (B) the lease line is **principal + interest**, both read off the cash-flow statement (`Lease liability principal payments` in financing, `Interest paid on leases` in operating) — MPG.NZ's first build deducted the $8.0m principal and called it the full payment, missing $4.5m/yr on flows of $2-5m
- [ ] **Working-capital sign matches the balance sheet**: a manufacturer or distributor with receivables + inventory > payables has `wc_capture` **negative** (growth consumes cash); positive capture is only for prepaid/deferred-revenue models
- [ ] **EBIT for cash tax is after D&A ex right-of-use depreciation** when leases are in the flows — otherwise the lease is charged twice, EBIT goes negative and a flat tax rate books a cash *credit*; tax is `max(0, EBIT) × rate`
- [ ] **Sanity multiples are lease-consistent**: ex-lease EV against EBITDA less lease cash, or lease-inclusive EV against post-IFRS-16 EBITDA — never ex-lease EV over post-IFRS-16 EBITDA (MPG.NZ read 2.7x that way; 6.4-8.4x consistently)
- [ ] **NCI charged once** — attributable-only flows XOR an NCI deduction in the bridge; FY0 NOPAT ties to reported attributable net income
- [ ] **Every stripped income stream has its source asset in the bridge**, and vice versa (cross-shareholdings and investment securities at fair value, not just cash)
- [ ] SBC rows kept explicit at 0% in SBC-zero markets; `band_position` set to null and the collapse explained rather than reported as 0%
- [ ] **Risk charged once** — no ad-hoc WACC premium stacked on probability-weighted scenarios, and no per-scenario WACC differences

The agent owns the remaining checks — anchors and vintage, the update/reconciliation
rules, and JSON validity. See `.claude/agents/dcf-analyst.md`.
