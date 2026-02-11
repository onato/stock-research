# Rocket Lab USA (RKLB) - Downloaded Financial Reports

## Company Information
- **Ticker**: RKLB (NASDAQ)
- **Company**: Rocket Lab USA, Inc.
- **SEC CIK**: 0001819994
- **Fiscal Year End**: December 31 (calendar year)
- **IPO**: August 2021 (via SPAC merger with Vector Acquisition Corporation)

## Downloaded Files Summary

### SEC Filings (18 files)

#### Annual Reports (10-K) - 4 files
1. **RKLB_10K_FY2021.htm** (4.4 MB) - Year ended Dec 31, 2021
2. **RKLB_10K_FY2022.htm** (6.2 MB) - Year ended Dec 31, 2022
3. **RKLB_10K_FY2023.htm** (4.2 MB) - Year ended Dec 31, 2023
4. **RKLB_10K_FY2024.htm** (2.3 MB) - Year ended Dec 31, 2024

#### Quarterly Reports (10-Q) - 14 files
**2021** (2 quarters - post-IPO)
- RKLB_10Q_Q3-2021.htm (575 KB) - Period ended ~Aug 3, 2021
- RKLB_10Q_Q4-2021.htm (1.8 MB) - Period ended ~Nov 15, 2021

**2022** (3 quarters)
- RKLB_10Q_Q1-2022.htm (2.5 MB) - Period ended Mar 31, 2022
- RKLB_10Q_Q2-2022.htm (3.4 MB) - Period ended Jun 30, 2022
- RKLB_10Q_Q3-2022.htm (4.3 MB) - Period ended Sep 30, 2022

**2023** (3 quarters)
- RKLB_10Q_Q1-2023.htm (2.8 MB) - Period ended Mar 31, 2023
- RKLB_10Q_Q2-2023.htm (3.6 MB) - Period ended Jun 30, 2023
- RKLB_10Q_Q3-2023.htm (3.3 MB) - Period ended Sep 30, 2023

**2024** (3 quarters)
- RKLB_10Q_Q1-2024.htm (1.8 MB) - Period ended Mar 31, 2024
- RKLB_10Q_Q2-2024.htm (2.2 MB) - Period ended Jun 30, 2024
- RKLB_10Q_Q3-2024.htm (2.3 MB) - Period ended Sep 30, 2024

**2025** (3 quarters)
- RKLB_10Q_Q1-2025.htm (1.2 MB) - Period ended Mar 31, 2025
- RKLB_10Q_Q2-2025.htm (1.4 MB) - Period ended Jun 30, 2025
- RKLB_10Q_Q3-2025.htm (1.6 MB) - Period ended Sep 30, 2025

## Data Coverage
- **Annual reports**: FY2021 through FY2024 (4 years)
- **Quarterly reports**: Q3-2021 through Q3-2025 (14 quarters)
- **Total historical data**: ~4 years since IPO

## File Format
All files are in HTML format (.htm) from SEC EDGAR, containing inline XBRL data. These files include:
- Financial statements (income statement, balance sheet, cash flow)
- MD&A (Management Discussion & Analysis)
- Risk factors
- Segment data
- Notes to financial statements

## Missing Files
- **10-K FY2020**: Pre-IPO, not available as public company
- **10-Q Q1/Q2-2021**: Pre-IPO quarters
- **Earnings presentations**: Investor relations site has technical issues preventing download

## Notes for Analysis
1. Rocket Lab went public via SPAC in August 2021, so historical data is limited
2. Company uses calendar year fiscal year (Jan 1 - Dec 31)
3. Files are HTML format - will need text extraction using BeautifulSoup (not pdftotext)
4. Earnings presentations contain operational KPIs not found in SEC filings
5. Key business segments: Launch Services (Electron), Space Systems, Neutron (in development)

## Next Steps
1. Extract text from HTML files using BeautifulSoup
2. Parse financial metrics (revenue, gross margin, operating expenses, cash, backlog)
3. Parse operational KPIs from MD&A sections:
   - Number of launches per quarter/year
   - Backlog value and contract count
   - Electron vs Space Systems revenue breakdown
   - Neutron development milestones
   - Customer metrics
4. Try alternative methods to obtain earnings presentations (8-K exhibits, investor relations outreach)

## Key Metrics to Track
- Revenue (Launch vs Space Systems)
- Gross margin %
- Operating expenses (R&D, SG&A)
- Net loss
- Cash and cash equivalents
- Backlog ($B)
- Launch cadence (launches per quarter)
- Launch success rate
- Electron launches vs Space Systems revenue mix
- Path to profitability timeline

## Sources
- SEC EDGAR: https://www.sec.gov/edgar/browse/?CIK=0001819994
- Rocket Lab Investor Relations: https://investors.rocketlabcorp.com/investor-relations
- Nasdaq RKLB page: https://www.nasdaq.com/market-activity/stocks/rklb
