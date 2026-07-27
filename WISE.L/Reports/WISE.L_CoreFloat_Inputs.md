# Wise Group plc — Core-vs-Float Workbook Inputs
Extracted 2026-07-21 from the FY2026 Form 20-F (USD, restated FY2024-25), GBP annual reports FY2022-FY2025, results releases, half-years, and quarterly trading updates through Q1 FY2027. All eight prompts run; sources cited per item. Companion data file: `WISE.L_Quarterly_KPIs.csv` (19 quarters, Q3 FY2022 → Q1 FY2027).

## Key findings (synthesis)

1. **Europe share holds near the placeholder**: ~27% of group net revenue flows through the Wise Europe SA licence vs the assumed 30% — scenario C/D move only modestly. (No entity-level revenue is disclosed anywhere; this is a supportable estimate, not a reported figure.)
2. **Net cash, not zero — but not all of it moves**: +$1.87bn of net corporate cash (reconciled exactly under two independent methods against Wise's own "Corporate cash" APM). **Of this, $550–750m (central $650m) is trapped as regulatory capital in licensed subsidiaries (§9, added 2026-07-24) → freely distributable at parent ≈ $1.10–1.30bn**, ~$1.10–1.30/share on ~1,000m shares. Still the single largest upward correction to any DCF input, but use the distributable figure for return-of-capital scenarios; the trapped slice still earns float yield and belongs in the valuation, just not in the "excess cash" line.
3. **The float is the business, for now**: ~85% of FY2026 pre-tax economics. The core transfer/card business ran between −1% and +6.6% pre-tax margin in FY2026 depending on cost allocation — Wise deliberately prices the core near cost and lives off float income.
4. **And the float is over-earning**: 2.22% retained yield vs ~1.5% normalized, mostly a UK regulatory windfall (the UK licence forbids paying customers interest, so UK balances run at 100% retention = 42% of group net interest). ~$110–160m of pre-tax run-rate is windfall that the benefit-sharing framework, competition, and the Wise Assets mix-shift all erode. Points 3–4 stack: the core-vs-float model will show far higher rate sensitivity than a consolidated flat-margin DCF.
5. **The tail risk is real and minimally disclosed**: the Brussels prosecutor is finalizing a direct criminal summons against the entity holding the entire EEA licence (€500m+ of allegedly laundered flows); the 20-F's only treatment is a one-paragraph Item 8 mention of a media-reported "inquiry" with a no-material-effect statement (corrected 2026-07-24 — earlier version of this memo said it was absent entirely). Unprovisioned and unquantified; scenario D fine parameters must be benchmarked externally.
6. **Share count**: use ~1,000–1,005m fully diluted, not the 1,029.7m backward-looking weighted average; the EST buyback tranche pre-funds SBC and does not shrink the count.

## Cell-mapped summary

| Workbook cell | Input | Value | Confidence |
|---|---|---|---|
| Assumptions B12 | Europe (Wise Europe SA licence) share of group net revenue | **~27%** (range 25–29%; ~30% of transaction revenue) | Judgment-supported estimate |
| Assumptions B10 | Net corporate cash | **+$1,874m ex-leases** (+$1,725m incl. leases) | Reconciles exactly under 2 methods |
| Assumptions B13/B14 | Retained net yield on avg customer balances | FY2026 actual **2.22%**; normalized **~1.5%** (1.4–1.6%) | Framework-anchored |
| Assumptions B9 | Fully diluted shares | **~1,000–1,005m** (vs 1,029.7m FY2026 weighted avg) | Verified from Note 8 |
| CoreDCF row 7 | Core (ex-float) pre-tax margin on transaction revenue | FY2026: **−1%** (all-opex-to-core) to **+3.5%** (allocated); ~6.6% FX-normalized. FY2024/25: 15–18% / 11–15% | Method-dependent |
| Scenarios (fines) | Quantified legal exposure / provision | Settled ~$4.7m; provision **$23.8m**; Belgian probe **unprovisioned & unquantified** | Filings + web |
| Scenario D | EU forced-restructuring bound | **[CORRECTED 2026-07-24]** Wise Europe SA is an NBB **Payment Institution** (20-F Item 4.B), NOT an EMI — own funds = max(€125k initial capital, scaled % of avg monthly payment volume: 4%/2.5%/1%/0.5%/0.25% bands per Royal Decree 27-Apr-2018), ≈ €13-18m estimated, not 2% of e-money (~$160m+). Cross-default exposure $1,119.1m guarantees + $331.1m EMTN unchanged | Partially disclosed |
| Assumptions B10 adj. | Trapped regulatory capital (deduct from B10 for distributable cash) | **−$550–750m (central −$650m)** → distributable ≈ **$1.10–1.30bn** (§9) | Group figures disclosed (Mar-2025 vintage); entity split estimated |

---

## 1. Europe revenue share → B12: ~27% (range 25–29%)

FY2026 geographic note (20-F Note 5; transaction revenue by customer address, interest by legal entity holding cash):

| $m FY2026 | Txn rev | Int income | Int expense | Net rev | % of group net |
|---|---|---|---|---|---|
| Europe (ex-UK) | 569.4 | 271.1 | (127.3) | 713.2 | 28.5% |
| United Kingdom | 329.1 | 257.2 | — | 586.3 | 23.4% |
| Asia-Pacific | 450.5 | 65.4 | — | 515.9 | 20.6% |
| USA | 261.9 | 160.1 | (56.8) | 365.2 | 14.6% |
| Rest of world | 282.7 | 52.3 | (12.8) | 322.2 | 12.9% |
| Total | 1,893.6 | 806.1 | (196.9) | 2,502.8 | 100% |

- Europe ex-UK share trend (transaction revenue): 33.2% (FY2022) → 31.9% → 30.6% → 30.2% → 30.1% (FY2026). Net-revenue share stable ~28.2–28.5% FY2024-26.
- Europe's **net** interest share is only 23.6% of group net interest — the Belgian entity pays ~65% of all customer yield ($127.3m of $196.9m); the UK pays zero (licence prohibition).
- Wise Europe SA corroborated as the only material EU entity: one of four guarantor subsidiaries (20-F Note 20), Belgium a top-3 tax jurisdiction (tax note), NBB a principal regulator, full-scope audit component, "Europe (Wise Europe SA)" Tier-1 market board (FY2025 annual).
- **No entity-level statutory revenue for Wise Europe SA is disclosed anywhere.** Estimate = EEA transaction revenue (85–100% of Europe ex-UK, hair-cutting for Switzerland/non-EEA Europe) + Europe net interest: $628–713m → 25.1–28.5%; point estimate **~27%**.
- Verdict vs the 30% placeholder: close enough that scenario C/D values move only modestly (~3pp lower on net revenue).

## 2. Net corporate cash → B10: +$1,874m (ex-leases)

Line-by-line (20-F, USD, 31-Mar-2026); replicates Wise's own "Corporate cash" APM (Results FY2025) within ~2%:

| Item | $m | Source |
|---|---|---|
| Cash and cash equivalents | +27,802.2 | Balance sheet |
| AFS debt securities (all customer-backing) | +4,582.7 | Note 11 |
| Receivables from payment processors | +73.9 | Note 12 |
| Receivables from customers | +146.1 | Note 12 |
| Customer balances (Wise accounts) | −29,958.7 | Note 16 |
| Outstanding money transmission liabilities | −295.5 | Note 16 |
| Payables to payment processors | −142.3 | Note 15 |
| **Corporate liquid assets** | **+2,208.4** | (cross-check via segregation method: identical) |
| EMTN £250m 5.10% due Nov 2030 (carrying) | −328.7 | Note 17 |
| Accrued EMTN interest | −6.0 | Note 17 |
| RCF drawn (undrawn capacity $437.1m) | 0.0 | Note 17 |
| **Net corporate cash ex-leases** | **+1,873.7** | |
| Operating leases | −149.0 | Note 10 |
| **Net corporate cash incl. leases** | **+1,724.7** | |

- Cash decomposition: $14,824.1m segregated safeguarding accounts + $7,592.1m safeguarded MMFs + $5,386.0m mixed remainder (incl. $1,324.0m corporate MMFs; $1,119.1m UK comparable-guarantee backing).
- Sensitivity: narrow (cash-only) $1,796m; broad (add partner/broker receivables, collateral $52.7m, interest receivable $34.6m) up to ~$2,132m.
- Regulatory-capital trap: **quantified 2026-07-24 in §9 — deduct $550–750m** (entity own-funds minimums $335–470m + buffers; overlaps the ~$680m Safeguarding-Guarantee covenant liquidity floor).
- **Replaces the prior DCF's "net debt ≈ 0" — worth ~$1.87/share on ~1,000m shares.**

## 3. Float economics → B13/B14: normalized retained yield ~1.5%

| | FY2024 | FY2025 | FY2026 | Q1 FY27 ann. |
|---|---|---|---|---|
| Gross interest income $m | 610.0 | 758.3 | 806.1 | 901.6 |
| Paid to customers $m | (157.0) | (205.7) | (196.9) | (209.2) |
| **Net interest $m** | **453.0** | **552.6** | **609.2** | **692.4** |
| Net yield on avg balances | ~3.0% (GBP-basis) | 2.77% | 2.22% | ~2.27% |
| Net yield on avg holdings (incl. AUC) | 2.88% | 2.20% | 1.72% | — |

- **Framework**: retain first 1% of gross yield + 20% of the excess; return 80% of excess to customers. Actual pass-through only ~45% of target — the UK licence prohibits paying interest/cashback, so UK runs at 100% retention ($257.2m net FY2026 = 42% of group net interest).
- **Full framework compliance at 2.75–3.0% blended gross yields ⇒ ~1.35–1.40%**; effective floor ~1.0% (first-1% always retained). Recommended normalized: **1.4–1.6% of avg balances** (≈1.1–1.2% on holdings, decaying — AUC growing ~55% YoY vs 24% for balances, earns Wise nothing on the interest line).
- Rate sensitivity (FY2025 annual): −100bp ⇒ −£141.4m gross / **−£86.1m net** of benefits (~60% flow-through). 79% of AFS book matures <1yr; MMFs AAA — reprices with short rates almost immediately.
- Implied normalized net interest ~$450–500m vs $609m reported: **~$110–160m of pre-tax run-rate is cyclical/regulatory windfall.**

## 4. Core ex-float margin → CoreDCF row 7

Core revenue = transaction revenue. Two methods (USD m):

| Core pre-tax margin | FY2024 | FY2025 | FY2026 |
|---|---|---|---|
| (a) Zero-cost float (all opex to core) | 14.9% | 11.4% | **−1.0%** |
| (b) Allocated (~4.2–4.4% of opex to float) | 18.5% | 15.3% | **+3.5%** |
| (b) + FX-swing normalized | 14.8% | 12.5% | **+6.6%** |

- No cost allocation disclosed (single segment). Float-attributable opex ≈ treasury team (small slice of "Other functions" = 8.7% of headcount), safeguarding insurance/surety fees, ~12% of G&A, ~5% of servicing, ~4% of tech.
- FY2026 opex +39.5% YoY is partly optical: USD translation, $102m FX swing in transaction expense ($59.6m loss vs $42.6m prior gain), listing-year G&A, advertising $100.5m (vs $62.5m).
- Cross-check: Wise's own "first 1% only" underlying FY2025 PBT ⇒ 10.9% ex-float margin, matching method (a).
- **~85% of FY2026 pre-tax economics come from the float.** Core alone does not support a 20%+ margin assumption.

## 5. Legal / fines → Scenario parameters

| Item | Status | Amount | Licence language |
|---|---|---|---|
| Belgian prosecutor probe (Wise Europe SA) | Direct summons being finalized; public 1-Jun-2026 | **Unprovisioned, unquantified**; €500m+ suspicious flows alleged | Only generic filing language; criminal AML conviction of the EEA-hub entity is the tail scenario |
| CFPB (Wise US) | Settled Jan-2025, amended May-2025 | Penalty cut $2.025m → **$44,955** + ~$450k redress | Compliance undertakings only |
| Six-state MMET AML | Settled Jul-2025 | **$4.2m** ($700k/state); quarterly reports to ~mid-2027; 3rd-party validation | No licence action |
| NBB remediation 2021/2022 (2024 = FT exposure date, not a confirmed separate inspection) | "Fully implemented" per Wise; never disclosed in any Wise filing | No fine disclosed | Same KYC theme as the criminal probe (escalation path) |
| OFSI (UK, Aug-2023) | Closed | £0 (disclosure only, "moderately severe") | None |

- Balance sheet: legal & regulatory provision **$23.8m** (FY2026) vs $17.6m (FY2025); Note 20: "no reasonable possibility… material loss" as of 31-Mar-2026. **[CORRECTED 2026-07-24: the 20-F DOES mention the Belgian matter — one paragraph in Item 8 Legal Proceedings, framed as a media-reported "inquiry" ("In June 2026, the media reported on an ongoing money laundering inquiry by the Brussels prosecutor's office relating to Wise Europe SA…"), followed by the no-material-effect statement. Still unprovisioned, no risk-factor naming Belgium/NBB, no licence discussion, no mention of €500m figure, direct summons, or criminal characterization. The 2022 NBB remediation plan has never been disclosed in any Wise filing.]**
- Scenario framing: base-case legal cost is noise (<$25m); scenario D fine parameter must be benchmarked externally (EU AML criminal settlements), not from Wise disclosures.

## 6. Growth vectors → CoreDCF/Float row 4

Data: `WISE.L_Quarterly_KPIs.csv`. Last-6-quarter YoY (USD):

| | Q4FY25 | Q1FY26 | Q2FY26 | Q3FY26 | Q4FY26 | Q1FY27 |
|---|---|---|---|---|---|---|
| Net revenue | n/a | +14.0% | +14.6% | +20.7% | +27.4% | +24.5% |
| Cross-border rev | +1.9%¹ | +7.7% | +12.9% | +19.8% | +29.0% | +22.0% |
| Card & other | +39.0%¹ | +36.5% | +26.9% | +35.3% | +38.0% | +38.5% |
| Net interest | n/a | +9.8% | +7.5% | +9.5% | +14.4% | +16.6% |
| Volume | +27.8%¹ | +31.3% | +29.1% | +30.4% | +35.2% | +26.0% (+24% cc) |
| Active customers | +17.4%¹ | +17.0% | +17.4% | +20.4% | +21.5% | +21.1% |
| Holdings | n/a | +42.5% | +37.2% | +44.0% | +40.3% | +30.8% |

¹ GBP-basis (no USD Q4 FY24 restatement exists).

- Accelerating: cross-border revenue (take-rate compression annualized out: −14bps → −2bps YoY; take rate 0.50% and now stable), card & other (27% of net revenue vs 20% two years ago), business customers (+15% → +28%, business volume +40-45%), net interest (holdings growth outrunning yield compression).
- Decelerating: holdings (balances slowing faster than the steady ~+55% Wise Assets/AUC), personal volume (~21%).

## 7. Shares / buyback → B9: use ~1,000–1,005m

- At 31-Mar-2026: Class A 1,025.67m issued; Class B 208.88m (9 votes, no economics — exclude); EST treasury 37.7m; awards outstanding 36.66m at WAEP $0.10; vested no-consideration issuable 21.0m.
- FY2026 EPS: basic WAS 1,019.5m, diluted 1,029.7m (+10.2m awards), basic $0.4892 / diluted $0.4843 — verified.
- Point-in-time fully diluted ≈ **1,024m**. Buyback (announced 25-Jun-2026): ">$500m", ~60% cancellation/treasury (~24m shares at $12.50) / ~40% EST. EST tranche does NOT reduce count (pre-funds SBC; EST 37.7m already exceeds awards 36.7m). Duration/method undisclosed.
- **Recommended: 1,005m (mid-execution) or 1,000m (completion basis).** Using 1,029.7m understates per-share value ~2.4–2.9%.

## 8. Safeguarding / capital → Scenario D bounds

- Licences: 70+ globally. Tier-1 entities: Wise Payments Ltd (UK/FCA), Wise Europe SA (Belgium/NBB — sole EEA licence, passported to 30 EEA states), Wise US Inc (CFPB/state MTLs), Wise Australia (APRA/AFSL). Wise Assets UK drives group MIFIDPRU consolidation (non-SNI).
- Group eligible capital £1.3bn (FY2025) = £1,297.5m CET1 vs £219.8m requirement, surplus £1,077.7m (MIFIDPRU 8 FY2025, obtained 2026-07-24, archived in PDFs/Extracted; whole-group scope). "Healthy buffers" at all entities — entity-level amounts still never disclosed; §9 builds the bottom-up estimates.
- Belgian framework **[corrected 2026-07-24]**: Wise Europe SA is an NBB **Payment Institution** (not EMI) — own funds = max(€125k, scaled payment-volume method) ≈ €13–16m estimated — small vs group capital.
- Safeguarding fully asset-backed 1:1 ($27.0bn segregated cash/MMFs/IG bonds vs $30.0bn customer liabilities, remainder settlement float); assets travel with liabilities in a restructuring — no customer-funds hole.
- Quantified cross-default web: the four key entities jointly guarantee the $331.1m EMTN, the RCF, and $1,119.1m safeguarding-guarantee indemnities (demand/cross-acceleration on insolvency or change of control, £20m threshold). RCF covenant: safeguarding-guarantee amount ≤ 3× Adjusted EBITDA.
- No safeguarding breach ever disclosed. No restricted-cash line or dividend-capacity disclosure — the true gaps for costing a forced EU restructuring.

---

## 9. Trapped regulatory capital → B10 adjustment: deduct $550–750m (added 2026-07-24)

Full analysis in conversation of 2026-07-24; primary docs archived: `WISE.L_MIFIDPRU8_FY2025.pdf/.txt` (+FY2024) in PDFs/Extracted.

**Group (disclosed, MIFIDPRU 8 as-at 31-Mar-2025, whole-group scope — regulatory consolidation = IFRS consolidation, OF2 p.18):** own-funds requirement **£219.8m** (FOR-bound; KFR £40.0m, PMR £6.4m); eligible own funds **£1,297.5m** all-CET1 (after £88.7m DTA/intangible deductions); surplus **£1,077.7m** (590%). FY2026 FOR roll-forward estimate ~£285–305m (~$380–410m). No FY2026 edition published yet (first will be at Wise Group plc level).

**Entity bottom-up (frameworks disclosed, amounts estimated — no entity own-funds figure disclosed anywhere):**

| Entity | Method | Est. minimum |
|---|---|---|
| Wise Payments Ltd (FCA EMI) | 2% Method D × est. $11.5–13.5bn WPL e-money + hybrid add-on | $240–290m |
| Wise Europe SA (NBB PI) | Volume method on ~€5.2–5.6bn/mo EEA volume | $15–18m |
| Wise US Inc | Max-state net worth (non-additive) + bonds | $10–30m |
| Wise Australia (APS 610) | 4% of est. $0.9–1.4bn stored value | $36–56m |
| Wise Assets UK + EE | PMR £750k / €150k, FOR-based | $3–7m |
| Other licences (SG/JP/BR/CA…) | Various | $30–70m |
| **Sum of minimums** | | **$335–470m** |
| **Held incl. buffers (1.3–1.6× on material entities)** | | **$480–720m** |

**Recommended: deduct $550–750m (central $650m) → freely distributable ≈ $1.10–1.30bn.** Cross-checks: eligible capital is 100% CET1 and fully cash-backed (corporate liquid assets $2,208m > equity $1,925m) — no other qualifying instruments absorb requirements. Buyback $500m+ ≈ 2.5× covered by distributable.

**Interacting constraint:** Safeguarding-Guarantee covenant (3) — aggregate insurance amount ($1,119.1m) ≤ cash + equivalents + undrawn RCF ($437.1m), tested semi-annually → on a corporate-cash reading, a **~$680m corporate liquidity floor** that largely overlaps the trapped-capital estimate (same cash satisfies both). Covenant "cash" definition undisclosed — if it includes customer cash, non-binding. MIFIDPRU 8 contains **zero** dividend/distribution-restriction language; 20-F Note 20: "no significant restrictions" on intra-group dividends/loans. Indemnities consume **no own funds** (K-ASA trivial), only liquidity capacity.

**Confidence:** group figures high (Mar-2025 vintage); WPL balance allocation medium-low (biggest swing factor); Wise Europe formula high/volume medium; US medium; AU formula high/SVL low; others low; buffer multiple low.

---

## What changed vs the standard DCF (context)
The project's standard `WISE.L_DCF.json` (2026-07-21) uses net debt ≈ 0, 1,029.7m shares, and a consolidated ~21% FCF margin. This extraction implies: +$1.87bn net cash, ~1,000-1,005m shares, and an economics split where the float (normalized ~$450-500m net interest at ~1.5% retained yield) carries ~85% of pre-tax profit while the core transfer/card business runs near breakeven (FY2026) — i.e., the core-vs-float model will show materially different risk exposure to rate cuts and to the Belgian licence tail than the consolidated DCF does.
