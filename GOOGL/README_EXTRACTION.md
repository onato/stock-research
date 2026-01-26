# GOOGL Financial Metrics Extraction

## Quick Start

Run the Python extraction script:

```bash
cd /Users/swilliams/Stocks/Research/GOOGL
python3 extract_googl_metrics.py
```

This will:
1. Parse all 28 extracted text files in `GOOGL/Extracted/`
2. Extract financial metrics for each period (Q1 2020 - Q3 2025, FY2015-FY2024)
3. Create CSV at `GOOGL/Reports/GOOGL_Metrics.csv`
4. Display summary of first and last 10 periods

## Extracted Metrics

### Standard Financial Metrics
- **Period**: Q1 2020, FY2020, etc.
- **Revenue**: Total revenues (in millions USD)
- **GrossProfit**: Revenue - Cost of revenues
- **GrossMargin**: Gross Profit / Revenue × 100
- **OperatingIncome**: Income from operations
- **OperatingMargin**: Operating Income / Revenue × 100
- **NetIncome**: Bottom-line profit
- **NetMargin**: Net Income / Revenue × 100
- **EPS**: Diluted earnings per share

### Balance Sheet (for DCF)
- **ShareholdersEquity**: Total stockholders' equity
- **TotalDebt**: Long-term debt + current portion
- **CashAndEquivalents**: Cash + marketable securities
- **SharesOutstanding**: Diluted shares (in millions)

### Cash Flow
- **FreeCashFlow**: Operating CF - CapEx

### Google-Specific Segments
- **GoogleSearchRevenue**: Google Search & other
- **YouTubeAdsRevenue**: YouTube advertising revenues
- **GoogleCloudRevenue**: Google Cloud revenues
- **GoogleNetworkRevenue**: Google Network revenues
- **OtherBetsRevenue**: Other Bets revenues
- **TrafficAcquisitionCosts**: TAC payments
- **NumberOfEmployees**: Total headcount

## CSV Output Format

```csv
Period,Revenue,GrossProfit,GrossMargin,OperatingIncome,OperatingMargin,NetIncome,NetMargin,EPS,FreeCashFlow,ShareholdersEquity,TotalDebt,CashAndEquivalents,SharesOutstanding,GoogleSearchRevenue,YouTubeAdsRevenue,GoogleCloudRevenue,GoogleNetworkRevenue,OtherBetsRevenue,TrafficAcquisitionCosts,NumberOfEmployees
Q1 2020,41159,22177,53.9,7977,19.4,6836,16.6,10.00,8445,201442,14768,119675,687,24502,4038,2777,5224,135,7452,123000
...
```

## Troubleshooting

### If extraction fails:
1. Check that Python 3 is installed: `python3 --version`
2. Ensure all .txt files exist in `GOOGL/Extracted/`
3. Check file permissions
4. Review error messages for specific files

### If metrics are missing:
- The PDF-to-text extraction may have formatting issues
- Some older filings may use different terminology
- Manual review of extracted .txt files may be needed

### Manual verification:
To check a specific period:
```bash
grep -i "total revenues" GOOGL/Extracted/GOOGL_10Q_Q1-2020.txt | head -5
```

## Data Quality Notes

- All dollar amounts are in millions USD
- Percentages shown to 1 decimal place
- EPS shown to 2 decimal places
- Missing data will appear as empty cells in CSV
- Segment revenue breakdown started in certain fiscal years
- Earlier filings may have less detailed breakdowns

## Files

- `extract_googl_metrics.py` - Main extraction script
- `GOOGL/Extracted/*.txt` - Source SEC filings (28 files)
- `GOOGL/Reports/GOOGL_Metrics.csv` - Output CSV
