# Owner-FCF DCF: non-US and cyclical adaptations

Read this file **only when the routing table sends you here**: a non-US filer, a
cyclical, a company with material leases, a minority interest, non-operating assets,
an SBC-zero market, or a quote currency that differs from the reporting currency.
A US tech or consumer-internet build needs none of it. It is section 7 of
`owner-fcf.md`, kept apart so the ~8k characters are not in context on every run.

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
