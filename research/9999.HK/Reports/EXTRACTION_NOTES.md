# NetEase (9999.HK / NTES) Financial Metrics Extraction

## Extraction Summary
Successfully extracted financial metrics from NetEase SEC filings (20-F and 6-K forms) covering Q1 2021 through Q3 2024.

## Data Sources
- **Annual Reports (20-F)**: FY2014-FY2024 (11 years of data available)
- **Quarterly Reports (6-K)**: Q1 2021 through Q3 2024

## Extracted Metrics

### Core Financial Metrics
- Revenue (Net revenues)
- Gross Profit
- Gross Margin (%)
- Operating Income
- Operating Margin (%)
- Net Income (attributable to shareholders)
- Net Margin (%)
- EPS (Diluted) - per ordinary share
- Free Cash Flow (Operating Cash Flow - CapEx)

### Balance Sheet Metrics
- Shareholders' Equity (NetEase Inc.'s shareholders' equity)
- Total Debt (Short-term loans + Long-term loans)
- Cash and Equivalents (Cash + Time deposits + Restricted cash)
- Shares Outstanding (Diluted weighted average)

### Business Segment Revenue
- Games and related value-added services (GameRevenue)
- Youdao revenue
- Cloud Music revenue
- Innovative businesses and others revenue

## Currency & Units
- **Currency**: RMB (Chinese Yuan)
- **Units**: Millions (RMB)
- All values are in RMB millions unless otherwise noted
- NetEase reports in USD on 20-F filings but original amounts are in RMB

## Data Notes

### ADS vs Ordinary Shares
- NetEase ADSs trade on NASDAQ under ticker NTES
- 1 ADS = 5 ordinary shares
- EPS reported here is per ordinary share (not per ADS)
- To get EPS per ADS, multiply by 5

### Missing Data Points
- **EBITDA**: Not directly reported in the filings; would need to be calculated (Operating Income + Depreciation & Amortization)
- Some quarterly balance sheet metrics are not available for all quarters (only reported at period ends)
- Q4 data is typically included in annual reports

### Calculation Notes
1. **Free Cash Flow** = Operating Cash Flow - Purchase of property, equipment and software
2. **Total Debt** = Short-term loans + Long-term loans
3. **Cash and Equivalents** = Cash + Time deposits (current + non-current) + Restricted cash

### Data Quality
- All metrics extracted from official SEC filings (Form 20-F and Form 6-K)
- Cross-validated where multiple years of data appear in same filing
- Quarterly data extracted from unaudited condensed consolidated statements
- Annual data from audited financial statements

## Coverage by Period

| Period | Revenue | Gross Profit | Operating Income | Net Income | Balance Sheet | FCF |
|--------|---------|--------------|------------------|------------|---------------|-----|
| Q1 2021 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Q2 2021 | ✓ | ✓ | ✓ | ✓ | ✓ | - |
| Q3 2021 | ✓ | ✓ | ✓ | ✓ | - | - |
| FY2021 | ✓ | ✓ | ✓ | ✓ | ✓ | - |
| Q1 2022 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Q2 2022 | ✓ | ✓ | ✓ | ✓ | ✓ | - |
| Q3 2022 | ✓ | ✓ | ✓ | ✓ | ✓ | - |
| Q4 2022 | ✓ | ✓ | ✓ | ✓ | ✓ | - |
| FY2022 | ✓ | ✓ | ✓ | ✓ | ✓ | - |
| Q1 2023 | ✓ | ✓ | ✓ | ✓ | - | ✓ |
| Q2 2023 | ✓ | ✓ | ✓ | ✓ | - | ✓ |
| Q3 2023 | ✓ | ✓ | ✓ | ✓ | - | ✓ |
| Q4 2023 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| FY2023 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Q1 2024 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Q2 2024 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Q3 2024 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

## Key Business Insights

### Revenue Segments (as of Q1 2024)
- **Games and related value-added services**: RMB 21,460M (79.9% of total revenue)
- **Youdao (education technology)**: RMB 1,392M (5.2%)
- **Cloud Music**: RMB 2,030M (7.6%)
- **Innovative businesses and others**: RMB 1,970M (7.3%)

### Margin Trends
- **Gross Margin**: Improved from 53.9% (Q1 2021) to 63.4% (Q1 2024)
- **Operating Margin**: Improved from 20.8% (Q1 2021) to 28.4% (Q1 2024)
- **Net Margin**: Improved from 21.6% (Q1 2021) to 28.4% (Q1 2024)

### Balance Sheet Strength
- **Cash Position**: RMB 136.1B as of Q1 2024 (up from RMB 87.6B in Q1 2021)
- **Total Debt**: RMB 25.3B as of Q1 2024 (up from RMB 21.8B in Q1 2021)
- **Shareholders' Equity**: RMB 127.3B as of Q1 2024 (up from RMB 85.7B in Q1 2021)
- **Net Cash Position**: Approximately RMB 110.8B (cash minus debt)

## Recommendations for Further Analysis

1. **EBITDA Calculation**: Add depreciation & amortization data to calculate EBITDA
2. **Historical Data**: Extract data from FY2014-FY2020 20-F filings for longer-term trends
3. **Segment Analysis**: Deep dive into segment-specific margins and growth rates
4. **Cash Flow Statement**: Extract detailed operating, investing, and financing cash flows
5. **KPIs**: Extract company-specific KPIs like DAU, MAU, paying users (if disclosed)

## DCF Readiness

The extracted data includes the key inputs needed for DCF valuation:
- ✓ Shareholders' Equity (for equity growth calculation)
- ✓ Total Debt (for net debt calculation)
- ✓ Cash and Equivalents (for net debt calculation)
- ✓ Shares Outstanding (for per-share valuation)
- ✓ Free Cash Flow (for cash flow projections)

Missing for comprehensive DCF:
- Long-term revenue growth rates (can be calculated from extracted data)
- WACC components (cost of equity, cost of debt) - need separate analysis
- Terminal growth rate assumption - requires management guidance/analyst estimates
