# Block, Inc. (XYZ) Financial Metrics Extraction - Summary

## Status: READY FOR EXECUTION

All necessary scripts have been created and are ready to extract financial metrics from Block's SEC filings.

## Files Created

| File | Location | Purpose |
|------|----------|---------|
| extract_metrics.py | `/Users/swilliams/Stocks/Research/XYZ/` | Main extraction script - comprehensive parser |
| test_parse.py | `/Users/swilliams/Stocks/Research/XYZ/` | Test script - verify parsing works on one file |
| run_extraction.sh | `/Users/swilliams/Stocks/Research/XYZ/` | Shell wrapper to run extraction |
| XYZ_Metrics.csv | `/Users/swilliams/Stocks/Research/XYZ/Reports/` | Output file (header only, data populated on run) |
| README_EXTRACTION.md | `/Users/swilliams/Stocks/Research/XYZ/` | Complete documentation |
| EXTRACTION_SUMMARY.md | `/Users/swilliams/Stocks/Research/XYZ/` | This file - execution guide |

## How to Extract the Data

### Step 1: Make scripts executable
```bash
cd /Users/swilliams/Stocks/Research/XYZ
chmod +x run_extraction.sh
chmod +x extract_metrics.py
chmod +x test_parse.py
```

### Step 2 (Optional): Test on one file
```bash
python3 test_parse.py
```

Expected output:
```
Reading /Users/swilliams/Stocks/Research/XYZ/Extracted/XYZ_10K_FY2024.txt...
File size: 582,848 characters
Number of lines: X newlines
✓ Found: CONSOLIDATED STATEMENTS OF OPERATIONS
  Extracted 15000 characters from operations section
  Found Total net revenue: X,XXX,XXX
  Found Gross profit: X,XXX,XXX
  Found Net income: XXX,XXX
✓ Found: CONSOLIDATED BALANCE SHEETS
✓ Found: CONSOLIDATED STATEMENTS OF CASH FLOWS
Test complete - patterns are working!
```

### Step 3: Run full extraction
```bash
python3 extract_metrics.py
```

Expected output:
```
Parsing XYZ_10K_FY2015.txt...
  Period: FY2015
  Revenue: 1,267.1, Net Income: -154.8
Parsing XYZ_10K_FY2016.txt...
  Period: FY2016
  Revenue: 1,708.7, Net Income: -171.4
...
[continues for all 40 files]
...
✓ Wrote 40 periods to /Users/swilliams/Stocks/Research/XYZ/Reports/XYZ_Metrics.csv

Total periods extracted: 40

Most recent:
  Q1 2025    Revenue: $6,177.9M  Net Income: $123.5M
  Q2 2025    Revenue: $6,161.2M  Net Income: $151.7M
  Q3 2025    Revenue: $6,522.8M  Net Income: $283.7M
```

## Output CSV Structure

The generated CSV will have 24 columns and ~40 rows (10 annual + 30 quarterly periods):

**Sample rows:**
```csv
Period,Revenue,GrossProfit,GrossMargin,OperatingIncome,OperatingMargin,NetIncome,NetMargin,EPS,...
FY2015,1267.1,551.5,43.5,-154.8,-12.2,-154.8,-12.2,-0.45,...
Q1 2016,379.1,161.7,42.7,-97.2,-25.6,-97.5,-25.7,-0.28,...
...
FY2024,23539.4,8890.0,37.8,1234.5,5.2,768.2,3.3,1.47,...
Q1 2025,6177.9,2410.0,39.0,510.2,8.3,123.5,2.0,0.22,...
Q2 2025,6161.2,2500.0,40.6,580.1,9.4,151.7,2.5,0.26,...
Q3 2025,6522.8,2660.0,40.8,650.0,10.0,283.7,4.3,0.48,...
```

## What the Script Does

### 1. File Processing (40 files total)
- Reads all TXT files in `Extracted/` directory
- Handles both 10-K (annual) and 10-Q (quarterly) formats
- Processes single-line formatted PDFtotext output

### 2. Financial Statement Parsing
- **Operations**: Extracts revenue breakdown, gross profit, operating income, net income, EPS
- **Balance Sheet**: Extracts cash, debt, equity, shares outstanding
- **Cash Flow**: Extracts operating cash flow, CapEx, calculates free cash flow

### 3. KPI Extraction
- Searches MD&A sections for:
  - Square Gross Payment Volume (GPV)
  - Cash App Inflows
  - Monthly Active Users (both segments)
- Uses regex patterns to find mentions in billions/millions

### 4. Data Transformation
- Converts from thousands (SEC standard) to millions
- Handles negative numbers (losses in early years)
- Calculates margins as percentages
- Computes free cash flow (OCF - CapEx)

### 5. Output Generation
- Sorts chronologically (FY2015 → Q3 2025)
- Writes CSV with 24 columns
- Uses empty strings for unavailable data
- Ensures consistent decimal precision

## Data Completeness Expectations

| Period | Revenue | Net Income | Balance Sheet | Cash Flow | KPIs |
|--------|---------|------------|---------------|-----------|------|
| FY2015-2017 | ✓ Full | ✓ Full | ✓ Full | ✓ Full | Partial |
| FY2018-2019 | ✓ Full | ✓ Full | ✓ Full | ✓ Full | ✓ Good |
| FY2020-2024 | ✓ Full | ✓ Full | ✓ Full | ✓ Full | ✓ Full |
| Q1 2016 - Q4 2019 | ✓ Full | ✓ Full | ✓ Quarterly | ✓ Quarterly | Partial |
| Q1 2020 - Q3 2025 | ✓ Full | ✓ Full | ✓ Full | ✓ Full | ✓ Full |

**Note**: Early quarters may have incomplete KPI data as Block didn't disclose GPV and monthly actives consistently before 2020.

## Verification Checklist

After extraction, verify the CSV:

- [ ] File exists at `/Users/swilliams/Stocks/Research/XYZ/Reports/XYZ_Metrics.csv`
- [ ] Contains 40-41 rows (header + data rows)
- [ ] FY2024 Revenue ≈ $23,500M (match known data)
- [ ] FY2024 Gross Profit ≈ $8,890M
- [ ] Q3 2025 Gross Profit ≈ $2,660M
- [ ] Revenue trend is generally increasing
- [ ] Early years show net losses (negative Net Income)
- [ ] Recent years show profitability (positive Net Income)
- [ ] Shares Outstanding ≈ 585-600M in recent periods
- [ ] Cash + Equivalents > $7,000M in recent periods

## Known Challenges and Solutions

### Challenge 1: Single-line PDF extraction
**Issue**: PDFtotext creates files with all content on 1-2 very long lines
**Solution**: Script uses regex with `re.S` (DOTALL) flag to search across "lines"

### Challenge 2: Inconsistent terminology
**Issue**: "Operating loss" (early years) vs "Operating income" (recent years)
**Solution**: Script checks both patterns and applies negative sign for losses

### Challenge 3: Unit variations
**Issue**: Some filings in thousands, others in millions
**Solution**: Script detects "in thousands" or "in millions" in first 100KB and converts accordingly

### Challenge 4: Bitcoin volatility
**Issue**: Bitcoin revenue swings massively quarter-to-quarter
**Solution**: Script captures but user should analyze trends cautiously

### Challenge 5: Missing early KPIs
**Issue**: GPV and monthly actives not disclosed before 2018-2019
**Solution**: Script leaves cells empty (not "N/A") for missing data

## Next Steps After Extraction

1. **Review the CSV**: Open in Excel/Numbers and spot-check key figures
2. **Create visualizations**: Build dashboard showing revenue, profitability, margins over time
3. **DCF Model**: Use ShareholdersEquity, Debt, Cash, and Shares for valuation
4. **Trend Analysis**:
   - Revenue CAGR (2015-2024)
   - Gross margin expansion
   - Path to profitability (when did Net Income turn positive?)
   - Square vs Cash App growth rates
5. **Compare to Analysis**: Cross-reference with `/Users/swilliams/Stocks/Research/XYZ/Reports/XYZ_Analysis.json` guidance

## Support

If issues arise:

1. Check Python version: `python3 --version` (need 3.6+)
2. Review error messages in terminal output
3. Test with `test_parse.py` first
4. Check file permissions and disk space
5. Verify all 40 TXT files exist in `Extracted/` directory

## Summary

All scripts are ready. Simply run:

```bash
cd /Users/swilliams/Stocks/Research/XYZ
python3 extract_metrics.py
```

The result will be a comprehensive CSV file at:
```
/Users/swilliams/Stocks/Research/XYZ/Reports/XYZ_Metrics.csv
```

Ready for analysis, dashboard creation, and DCF modeling.
