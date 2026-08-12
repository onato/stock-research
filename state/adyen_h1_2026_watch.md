# ADYEN.AS H1-2026 watch — pre-committed read criteria

Written **2026-08-12, before the print**, so the growth verdict is a measurement, not a
rationalisation. Letter drops **13-Aug-2026 07:00 CEST** (17:00 NZST), call 15:00 CEST.

Source of record: `investors.adyen.com/financials/h1-2026/_payload.json` → Frontify PDF.
Fallback: investors.adyen.com/news, Euronext RNS.

## The question
Is net revenue growth re-accelerating, or still rolling over?

## Baseline (what we already know)
| Period | Net rev (€m) | Reported YoY | CC YoY |
|---|---|---|---|
| FY2024 | 1,996.1 | +23% | +25% |
| H1 2025 | 1,093.5 | +19.7% | — |
| Q3 2025 | 598.4 | — | — |
| H2 2025 | 1,270.7 | +17.4% | — |
| FY2025 | 2,364.2 | +18% | +21% |
| **Q1 2026** | **620.8** | **+16%** | **+20%** |

Trend so far: reported growth 23% → 19.7% → 17.4% → 16%. **Four consecutive
decelerating prints.** Digital pillar (56% of revenue) was +9% reported in Q1-2026,
the slowest pillar print in company history.

FY2026 guidance: **20–22% CC** net revenue growth. 55%+ EBITDA margin target pushed
2026 → 2028.

## Pre-committed thresholds — H1 2026 net revenue vs H1 2025 (€1,093.5m)

| Verdict | Reported YoY | Implied H1-26 net rev | Read |
|---|---|---|---|
| **RE-ACCELERATION** | ≥ +19% | ≥ €1,301m | Breaks the 4-print downtrend. Q1 was the trough. Bull case live. |
| **STABILISATION** | +16.5% to +19% | €1,274–1,301m | Deceleration arrested, not reversed. Base case intact. |
| **CONTINUED DECEL** | +13% to +16.5% | €1,236–1,274m | Downtrend intact. Base case IV is if anything generous. |
| **BREAKDOWN** | < +13% | < €1,236m | Thesis break. CC guidance likely cut. Bear case → base. |

Note: Q1-2026 already printed 620.8. So H1 total ≥ €1,301m requires Q2 ≥ €680m
(+21.5% on the implied Q2-2025 ~€472.7m). Sanity-check the Q2 stub, don't just read
the half.

## Secondary reads (all must be checked, they can override the headline)
1. **CC vs reported spread.** FY2025 was 18% rep / 21% CC. If reported improves only
   because EUR weakened, that is FX, not demand. **Weight CC growth, not reported.**
2. **Digital pillar growth.** The single most important number in the letter. +9%
   reported in Q1-26. Below +9% = deterioration regardless of group headline.
   Platforms (+50% FY2025) and Unified Commerce are the offsets.
3. **Take rate.** 22bps (2018) → 14.7 (H1-24 trough) → 17.1 (H2-25) → 16.3 (Q1-26).
   The **June-2026 large-merchant pricing revision** should start biting here. A take
   rate < 16.0bps means pricing pressure is real and structural, not mix.
4. **EBITDA margin.** H1-25 was 49.7%, H2-25 55.0%. H1 is seasonally weaker. Watch for
   any further push-out of the 55%+/2028 target.
5. **FY2026 guidance action.** Reaffirm / raise / cut of the 20-22% CC. A cut is the
   single most thesis-relevant sentence in the document.
6. **Leadership.** CFO Tandowsky departs 31-Aug-2026 (interim Hwa Tsao 1-Sep).
   Co-CEO van der Does health step-back. Any further change = execution risk.
7. **Talon.One (€750m) / Orb ($335m)** closed 1-Jul-2026 — AFTER the H1 period end,
   so they should NOT contribute to H1 revenue. If contribution appears, check the
   accounting; any inorganic revenue must be stripped before comparing growth.

## Valuation hooks — GRADE AGAINST THE WORKBOOK, NOT THE JSON

**Primary model of record: `research/ADYEN.AS/DCFs/ADYEN_DCF_v1_preH1FY26.xlsx`**
(v1.0, 9-Aug-2026, Stephen's own framework, house 15/50/35 weights, WACC 10.5%,
20x terminal cap, float stream valued separately at 12x.)

| | Bear | Base | Bull |
|---|---|---|---|
| Value/share | €571.11 | €1,047.67 | €1,418.27 |
| vs €933.20 | −38.8% | **+12.3%** | +52.0% |
| Weight | 15% | 50% | 35% |
| **FY2026E reported growth** | **15%** | **18%** | **20%** |

- **Probability-weighted value €1,105.89 vs price €933.20 → +15.6% margin of safety.**
- 15%-hurdle entry: weighted **€695.66** (base €669.14).
- Float stream = only 5.9% of weighted value.

**NOTE THE CONFLICT — say this out loud in the verdict.** The workbook says Adyen is
*undervalued* (+15.6% weighted). The older JSON DCF (`ADYEN.AS_DCF.json`, base IV
€749.38, −19.4%) says *overvalued*. They differ mainly on terminal treatment and
scenario weights, not on the facts. The workbook is the newer, hand-checked model and
is the one to grade against; report the JSON only as the bearish cross-check.

### The exact number H1 tests
Base assumes **18% reported FY2026** growth (≈22% cc, i.e. the low end of the 20-22%
cc guide after ~4pp FX drag). Bear 15%, bull 20%.

H1-2026 reported YoY maps to a scenario directly:
- **≥20%** → bull driver validated
- **~18%** → base holds, €1,047.67 stands, stock still cheap
- **~15%** → bear driver live, weighted value falls toward the price
- **<15%** → below even bear FY26; the +15.6% margin of safety is gone

### Reverse read (the sanity anchor)
At 10.5% required return and flat 53% EBITDA margin, **€933 already implies only ~13%
net revenue CAGR for a decade** — about a third below management's ~20% ambition. So
the market has ALREADY priced substantial deceleration. This is the crux: H1 has to be
bad enough to break a 13% decade CAGR, not merely bad versus a 20% guide. Growth
slowing from 18%→16% is NOT automatically bad news at this price.

### Known gaps to fix from the H1 print (flagged in Model_Info)
- SBC % of net revenue is an **estimate of 3.0%** — replace from the share-based
  payments note. (JSON says actual FY2025 SBC was €43.5m = 1.8% of net revenue, so
  3.0% is likely too HIGH → fixing it should *raise* value.)
- Owner net cash €4,200m is an **estimate** at 1-Jul-26 post Talon.One+Orb — replace
  with reported, merchant funds excluded.
- Diluted share count 31.55m — replace with H1'26 reported diluted.
- IFRS quirk: reported EBITDA margin is **already net of SBC** (unlike US-GAAP).
  Row 7 is PRE-SBC. Do not double-count.
- Owner FCF (JSON convention) = Reported FCF − SBC − Finance income.
- Net cash = own cash €4,987.6m − leases = €4,734.8m. NEVER the raw €10.8bn.

## Do-not-repeat traps (from memory)
- H1/H2 letters and Q1/Q3 updates OVERLAP — never sum Q1-2026 into H1-2026.
- Working capital is a net USE of cash (sign error cost ~€42-45m/yr once).
- Strip Finance income (own-cash interest) — 29% haircut to the FCF base.
- Download new filings into `research/ADYEY/PDFs/` and RE-STAGE into ADYEN.AS,
  or ADYEN.AS silently goes stale.
