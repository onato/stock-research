# Pinterest (PINS) Financial Metrics - Data Extraction Summary

## File Location
`/Users/swilliams/Stocks/Research/PINS/Reports/PINS_Metrics.csv`

## Data Coverage
- **Time Period**: Q1 2019 through Q3 2025
- **Total Periods**: 34 periods (27 quarterly + 7 annual)
- **Data Sources**: 10-K annual reports, 10-Q quarterly reports, and earnings presentations

## Metrics Included

### Core Financial Metrics (in millions USD)
1. **Revenue** - Total revenue for the period
2. **GrossProfit** - Revenue minus cost of revenue
3. **GrossMargin** - Gross profit as percentage of revenue
4. **OperatingIncome** - Income from operations (EBIT)
5. **OperatingMargin** - Operating income as percentage of revenue
6. **EBITDA** - Adjusted EBITDA (non-GAAP)
7. **NetIncome** - Bottom-line profit/loss
8. **NetMargin** - Net income as percentage of revenue
9. **EPS** - Diluted earnings per share (in dollars)

### Cash Flow Metrics (in millions USD)
10. **OperatingCashFlow** - Cash from operating activities
11. **FreeCashFlow** - Operating cash flow minus CapEx

### Balance Sheet Metrics (in millions USD)
12. **ShareholdersEquity** - Total shareholders' equity (book value)
13. **TotalDebt** - Total debt (Pinterest has been essentially debt-free)
14. **CashAndEquivalents** - Cash + marketable securities
15. **SharesOutstanding** - Diluted shares outstanding (in millions)

### User Metrics (Platform KPIs)
16. **MAU_Global** - Monthly Active Users globally (in millions)
17. **MAU_US_Canada** - MAUs in US & Canada (in millions)
18. **MAU_Europe** - MAUs in Europe (in millions)
19. **MAU_RestOfWorld** - MAUs in Rest of World (in millions)

### Monetization Metrics (in USD)
20. **ARPU_Global** - Average Revenue Per User globally
21. **ARPU_US_Canada** - ARPU in US & Canada
22. **ARPU_Europe** - ARPU in Europe
23. **ARPU_RestOfWorld** - ARPU in Rest of World

## Key Data Notes

### Pinterest-Specific Considerations
1. **IPO**: Pinterest went public in April 2019, which is why FY2019 shows unusually high losses due to IPO-related share-based compensation ($1.38B)
2. **Debt-Free**: Pinterest has maintained a debt-free balance sheet (TotalDebt = 0.0 throughout)
3. **Geographic Revenue**: Revenue and ARPU are calculated based on user geography when performing revenue-generating activities
4. **MAU Definition**: Monthly Active User = authenticated user who visits website/app or interacts via browser extension at least once in 30-day period
5. **Adjusted EBITDA**: Non-GAAP measure that excludes share-based compensation, depreciation, amortization, and other non-recurring items

### Data Quality & Completeness
- **Complete Data**: All quarterly and annual periods have revenue, profitability, and user metrics
- **Partial Data**: Some quarterly periods lack balance sheet data (OperatingCashFlow, FreeCashFlow, ShareholdersEquity)
  - Balance sheet metrics are primarily available for year-end (Q4) and recent quarters
  - Cash flow metrics (OCF, FCF) are only available on annual basis from 10-K filings
- **Most Recent**: Q3 2025 data is complete through operating metrics; balance sheet data available

### Special Periods
- **FY2019**: Negative operating margin (-121.6%) due to $1.38B in share-based compensation from IPO
- **FY2024**: Exceptional net income ($1,862.1M, 51.1% margin) due to $1.6B deferred tax asset release
- **Q4 2024**: Shows the tax benefit impact ($1,815.3M net income)

## DCF Valuation Readiness

### Available for DCF Analysis
The following metrics required for DCF valuation are available for recent periods:
- **ShareholdersEquity**: Available for year-end periods (FY2019-FY2024) and Q1-Q3 2025
- **TotalDebt**: 0.0 for all periods (debt-free)
- **CashAndEquivalents**: Available for all year-end periods and recent quarters
- **SharesOutstanding**: Available for all periods

### Recommended Approach
For DCF modeling, use:
- **Latest Balance Sheet**: Q3 2025 data (ShareholdersEquity: $5,220.0M, Cash: $2,720.0M, Shares: 703.0M)
- **Historical Cash Flows**: FY2020-FY2024 annual data
- **Growth Metrics**: Track revenue CAGR, ARPU growth, and MAU expansion

## Data Verification
All data extracted from official SEC filings (10-K, 10-Q) and company earnings presentations. Key metrics cross-referenced across multiple sources for accuracy.

## Business Trends Visible in Data

### Revenue Growth
- **FY2019**: $1,142.8M
- **FY2024**: $3,646.2M
- **CAGR**: ~26% (2019-2024)

### Profitability Improvement
- **Gross Margin**: 68.6% (FY2019) → 79.4% (FY2024)
- **Operating Margin**: -5.5% (FY2019) → 4.9% (FY2024)
- Turned operating profitable in FY2024

### User Growth
- **Global MAUs**: 300M (FY2019) → 600M (Q3 2025)
- **Doubled user base** in ~6 years

### Monetization Enhancement
- **Global ARPU**: $3.81 (FY2019) → $7.64 (Q3 2025)
- **US/Canada ARPU**: $9.04 (FY2019) → $31.96 (Q3 2025)
- Strong pricing power, especially in core markets

### Cash Generation
- **Free Cash Flow**: -$33.1M (FY2019) → $940.0M (FY2024)
- **FCF Margin**: -2.9% → 25.8%

## Geographic Mix Evolution
### FY2019 vs Q3 2025
- **US/Canada MAUs**: 28% → 17% of total (still highest ARPU)
- **Europe MAUs**: 32% → 25% of total
- **Rest of World MAUs**: 40% → 58% of total
- Clear shift toward international user base, though monetization remains US-heavy

---

**Last Updated**: 2026-01-22
**Data Through**: Q3 2025 (Sep 30, 2025)
