---
name: dcf-methods
description: Valuation methodologies for the research folder — routes a ticker to the right model (owner-FCF DCF, AFFO capitalization, residual income, BVPS compounding, NAV/NTA, asset waterfall) and holds the detailed engine for each. Use when building or updating a {TICKER}_DCF.json, or when deciding which valuation model a company needs.
allowed-tools: Read, Write, Bash, Glob
---

# Valuation methods

**Pick the model from the business, not from habit.** The owner-FCF DCF is the default
for operating companies and wrong for roughly a third of this folder. Applying it to a
bank, a REIT or a listed investment company does not produce a conservative answer — it
produces a meaningless one, because the inputs it needs (free cash flow, net debt,
EBITDA) either do not exist or do not mean what the engine assumes.

## Routing table

Read the business first — the Analysis JSON, the segment note, what the company actually
does with capital. Then route:

| Business | Model | Reference |
|---|---|---|
| Operating company: tech, SaaS, consumer internet, marketplaces | **Owner-FCF DCF**, scenario-weighted | `references/owner-fcf.md` |
| Operating company, non-US or cyclical: industrials, consumer, keiretsu suppliers | **Owner-FCF DCF** + the section 7 adaptations (currency, leases, NCI, non-operating assets, SBC-zero) | `references/owner-fcf.md` |
| REIT / property trust (IAS 40 fair-value, external manager) | **AFFO or distributable-profit capitalization at cost of equity** | below |
| Bank, non-bank deposit taker, thrift | **Residual income on tangible book** | below |
| Insurer, holdco, compounder-of-book | **BVPS compounding × exit P/B** | below |
| LIC, investment trust, closed-end fund, VC unit trust | **NAV/NTA + exit discount** | below |
| Land subdivider, inventory-heavy developer | **Blended NTA exit-P/B and earnings exit-P/E** | below |
| Distressed, receivership, delisted, cash shell | **Asset or creditor waterfall — not a going-concern model** | below |
| Live takeover, scheme, strategic review | **Deal-completes vs deal-breaks weighting**, not three growth scenarios | below |

When a ticker straddles two rows (a REIT with a big operating business, a holdco that
also trades), value the parts separately and sum. Say in the JSON that you did.

## Owner-FCF DCF

**Read `references/owner-fcf.md` in full before building.** It carries the SBC and
interest adjustments, the component margin build, scenario construction and weighting,
the DCF and entry-price formulas, the workbook spec, the non-US/cyclical adaptations
and the method checklist.

The engine is not US-tech-only. Non-US and cyclical operating companies use the same
engine plus **section 7**, which covers currency-matched WACC, the single-lease-treatment
rule, minority interests, the excluded-income/matching-asset rule, SBC-zero markets, and
charging risk exactly once. Read section 7 before any such build — skipping it is worth
four figures per share on a Japanese supplier.

## The non-FCF models

These share one principle: **discount the flow the business actually produces, at the
rate appropriate to whom it accrues.** Common traps, all of them observed in this
folder:

- **Never `FCF ÷ WACC` on a post-interest flow.** AFFO, distributable profit and
  residual income are already after financing cost. Capitalize them at the **cost of
  equity** and never subtract net debt afterwards — that deducts the debt twice.
- **Deposits are not net debt.** For a bank or deposit taker they are operating funding.
  `TotalDebt = 0` is usually correct, and OCF can be wildly negative while the business
  is fine.
- **Fair-value movements are not earnings.** A REIT's or trust's reported net income is
  dominated by revaluation noise; NetMargin and P/E are meaningless. Use the
  distribution or NAV series.
- **Reported FCF can be structurally negative without distress** — land subdividers
  building inventory, infrastructure in a capex supercycle. Do not read it as a signal.
- **A dead or suspended ticker has no forward model.** Value the assets against the
  claims and stop; do not project growth for a company in receivership.

Each of these families is documented per-ticker in the user's memory (`MEMORY.md` has
one line per researched ticker, with the model that was used and why). **Read the
ticker's memory entry before choosing** — for a name already researched, the model
decision has usually been made and justified, and silently switching models invalidates
comparison against the prior valuation.

## Output contract

Whatever the model, the agent writes the same `{TICKER}_DCF.json` shape so the dashboard
and screener keep working: three scenarios with intrinsic values, probability weights
summing to 1.0, a canonical `probability_weighted.weighted_iv` denominated in the
**quote** currency, and an entry price. `.claude/agents/dcf-analyst.md` owns that
contract. A non-FCF model fills the same fields — it just derives them differently, and
`inputs.notes` must say which model was used and why.
