You are running headlessly, triggered automatically the moment Adyen's H1-2026
shareholder letter was published. Stephen is NOT watching. Your job is to read the
letter, grade it, and PING HIM with the verdict via Telegram. He should not have to
ask you for anything.

Working directory: /Users/swilliams/Stocks/Research

## Steps

1. Read `state/adyen_h1_2026_watch.md` in full. It holds the read criteria, written
   BEFORE the print deliberately so the verdict is a measurement, not a story fitted
   to the result. Follow it. Do not invent new thresholds.

2. Read the letter: `research/ADYEN.AS/Extracted/ADYEN.AS_Letter_H1-2026.txt`
   (fallback `research/ADYEY/Extracted/ADYEY_Letter_H1-2026.txt`).

3. Extract, and state each as a number:
   - H1-2026 net revenue (EURm) and YoY vs H1-2025 (EUR1,093.5m), BOTH reported and
     constant-currency
   - Pillar growth: Digital, Unified Commerce, Platforms (cc where given)
   - Take rate (bps), EBITDA and EBITDA margin, SBC (and as % of net revenue)
   - Owner net cash EXCLUDING merchant funds in transit; diluted share count
   - Any change to the 20-22% cc FY2026 guidance, and the 55%+/2028 margin target

4. Traps to avoid (these have bitten before):
   - Do NOT sum Q1-2026 into H1-2026 — the periods OVERLAP.
   - Weight CONSTANT-CURRENCY over reported; EUR moves distort reported growth.
   - Talon.One/Orb closed 1-Jul-2026, AFTER period end — strip any inorganic revenue
     before comparing growth.
   - IFRS puts SBC inside opex, so the reported EBITDA margin is ALREADY net of SBC.
   - Never use raw balance-sheet cash (~EUR10.8bn); most is merchant float.

5. Grade it. The model of record is the WORKBOOK
   `research/ADYEN.AS/DCFs/ADYEN_DCF_v1_preH1FY26.xlsx`, SBC-corrected to 1.85%:
   weighted EUR1,118.31 vs price EUR933.20 = +19.8%. (The older
   `Reports/ADYEN.AS_DCF.json`, base EUR749.38, is the bearish cross-check only.)

   **THE CENTRAL JUDGEMENT — do not grade the headline YoY.** Measured sensitivity:
   FY26 growth could fall 20% -> 8% and still leave ~+11% upside (~EUR1.30/share per
   point). But a PERMANENT ~-3pp shift across the whole decade path is break-even at
   EUR933. The price already implies only ~13% decade CAGR vs management's ~20%
   ambition, so heavy deceleration is ALREADY priced in.

   So the question is NOT "did growth slow?" but "is this slowdown PERMANENT?"
   - FX / mix / one-off / timing -> noise, largely irrelevant to value
   - Take-rate compression / competitive loss / structural -> repricing, act

   You may re-run `research/ADYEN.AS/DCFs/replicate_dcf_v1.py` (reproduces the
   workbook to within EUR0.11/share) with revised growth to quantify the impact.

6. Write your full analysis to `state/adyen_h1_2026_verdict.md` — numbers, the
   scenario read, and what it does to weighted value vs the EUR933.20 price.

7. **Send the Telegram** by calling the script DIRECTLY with Bash:
   `bash ~/.claude/skills/telegram/send.sh "your message"`
   Do NOT invoke the telegram *skill* — its loader hits a permission check in headless
   mode. The script itself is allow-listed and verified working. It prints "Sent." on
   success; if it does not, retry once, then try again with a shorter message.
   This is the whole point of the run — do not skip it, and do not use MarkdownV2
   (escaping breaks it). Keep it
   under ~1000 chars, plain text, and lead with the answer:
   - Direction: is growth picking up or turning down?
   - GOOD or BAD news for the holding, and why — the durability call, not the headline
   - Key numbers: H1 net revenue, reported and cc YoY, Digital pillar, take rate
   - Valuation: revised weighted value vs EUR933.20, and whether the +19.8% holds
   - Any guidance change
   - One line on what you'd watch or do next

If the letter cannot be read or parsed, still send a Telegram saying so plainly with
the reason. Never stay silent — silence is the one outcome that fails him.
