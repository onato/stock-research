# BYD Electronic (0285.HK) - Financial Data Extraction Notes

## Data Sources
Financial metrics extracted from:
- Annual Reports: FY2015, FY2016, FY2020, FY2022 (BYD Electronic)
- Interim Reports: H1 2021, H1 2022, H1 2023, H1 2024

## Important Notes

### Missing Data
- **FY2017, FY2023, FY2024**: Annual reports in the extracted files are for PC Partner Group Limited (1263.HK), not BYD Electronic (0285.HK). These periods are marked as "No data available."
- **OperatingProfit**: Not separately disclosed in BYD Electronic financial statements. The company reports directly from Gross Profit to Profit Before Tax with various expense line items.

### Currency
All financial figures are in **RMB millions**, consistent with the company's reporting currency.

### Shares Outstanding
- **2,253.2 million shares** (diluted) - consistent across all periods
- Share capital: RMB 4,052.2 million (HKD 0.001 par value per share)

### Segment Reporting
BYD Electronic reports as a **single operating segment** for management purposes. While the company operates in multiple business areas (smartphones, NEV components, AIDC, smart home, etc.), these are not reported as separate financial segments in the audited financial statements.

According to Note 3 of the FY2022 Annual Report:
> "For management purposes, the Group is organized into one operating segment based on industry practice and management's vertical integration strategy."

### Business Areas (Qualitative)
The company's main business areas include:
1. **Mobile handset components and modules** - smartphones, tablets
2. **New energy vehicle (NEV) components** - intelligent cockpit, ADAS, thermal management
3. **AIDC (AI Data Center)** - AI servers, liquid cooling, power management
4. **Other products** - smart home, gaming hardware, IoT devices, robots, communication equipment

Revenue breakdown by product line is not consistently disclosed in audited financial statements.

### Data Quality
- Annual report data: Audited by Ernst & Young
- Interim report data: Unaudited, reviewed
- All figures rounded to 1 decimal place (RMB millions)

### Key Financial Observations
1. **Revenue Growth**: Strong growth from FY2011 (15.9B) to H1 2024 (78.6B annualized ~157B)
2. **Margin Pressure**: Gross margin compressed from 13.2% (FY2020) to 6.8% (H1 2024)
3. **Debt**: Company was debt-free through FY2019, took on debt from FY2020 onwards
4. **Profitability**: Net margin compressed significantly from 7.4% (FY2020) to 1.9% (H1 2024)

### DCF Valuation Requirements
The CSV includes the minimum required fields for DCF analysis:
- ✓ ShareholdersEquity (Book Value)
- ✓ TotalDebt
- ✓ CashAndEquivalents
- ✓ SharesOutstanding
- ✓ NetProfit (for equity growth calculation)

### Historical Context
- **IPO**: Listed on HKEX on December 20, 2007
- **Parent Company**: BYD Company Limited (1211.HK / 002594.SZ)
- **Spin-off**: From BYD Company Limited in 2007

## File Paths
- **CSV Output**: `/Users/swilliams/Stocks/Research/0285.HK/Reports/0285.HK_Metrics.csv`
- **Source PDFs**: `/Users/swilliams/Stocks/Research/0285.HK/PDFs/`
- **Extracted Text**: `/Users/swilliams/Stocks/Research/0285.HK/Extracted/`

## Data Validation
Cross-referenced with:
- 5-year financial summaries in annual reports
- Notes to financial statements
- Management discussion and analysis sections
- Consolidated statements of profit or loss and financial position

## Last Updated
February 3, 2026
