# NVIDIA (NVDA) Financial Reports Download Summary

**Download Date:** February 2, 2026

## Overview
Successfully downloaded 67 financial documents covering over 11 years of NVIDIA's financial history, including SEC filings and earnings presentations.

## Files Downloaded

### SEC 10-K Annual Reports (11 files)
Complete fiscal year filings from FY2015 through FY2025:
- NVDA_10K_FY2015.htm through NVDA_10K_FY2025.htm
- Coverage: 11 consecutive fiscal years
- Note: NVIDIA's fiscal year ends in late January (e.g., FY2025 ended January 26, 2025)

### SEC 10-Q Quarterly Reports (36 files)
Quarterly filings for Q1, Q2, and Q3 (Q4 is included in 10-K):
- Q1-FY2015 through Q3-FY2026
- 36 quarterly reports total
- Missing: Q2-FY2020 and Q3-FY2020 (downloaded separately)

### Earnings Presentations (20 files)
Investor presentations and CFO commentary documents:
- Quarterly investor presentations: Q1-FY2021 through Q3-FY2026
- CFO Commentary docs: Q1-FY2023, Q3-FY2022, Q3-FY2023, Q3-FY2024
- These contain operational KPIs (DAU, MAU, etc.) NOT found in SEC filings

## File Locations
```
/Users/swilliams/Stocks/Research/NVDA/
├── PDFs/
│   ├── NVDA_10K_FY2015.htm
│   ├── NVDA_10K_FY2016.htm
│   ├── ... (11 annual reports)
│   ├── NVDA_10Q_Q1-FY2015.htm
│   ├── ... (36 quarterly reports)
│   └── Presentations/
│       ├── NVDA_Presentation_Q1-FY2021.pdf
│       ├── NVDA_CFO_Q1-FY2023.pdf
│       └── ... (20 presentations)
```

## Total Storage
184 MB total

## Data Sources

### SEC EDGAR
- Primary source for 10-K and 10-Q filings
- NVIDIA CIK: 0001045810
- URL: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001045810

### Investor Relations (q4cdn.com)
- Earnings presentations and CFO commentary
- URL pattern: https://s201.q4cdn.com/141608511/files/doc_financials/...

## File Formats
- SEC filings: HTML format (.htm) - iXBRL tagged documents
- Presentations: PDF format (.pdf)

## Coverage Timeline
- **10-K Annual:** FY2015 (ended Jan 2015) → FY2025 (ended Jan 2025)
- **10-Q Quarterly:** Q1-FY2015 (Apr 2014) → Q3-FY2026 (Oct 2025)
- **Presentations:** Q1-FY2021 (May 2020) → Q3-FY2026 (Nov 2025)

## Key Notes

1. **Fiscal Year Convention:** NVIDIA's fiscal year ends in late January
   - FY2025 = Feb 2024 through Jan 2025
   - FY2026 = Feb 2025 through Jan 2026

2. **File Naming:** Follows standardized format:
   - Annual: `NVDA_10K_FY{YEAR}.htm`
   - Quarterly: `NVDA_10Q_Q{1-3}-FY{YEAR}.htm`
   - Presentations: `NVDA_Presentation_Q{1-4}-FY{YEAR}.pdf`

3. **HTML vs PDF:**
   - SEC filings are HTML (can be converted to text for parsing)
   - Presentations are PDF (may require OCR for some content)

4. **Operational KPIs:**
   - SEC filings contain: Revenue, profit, cash flow, balance sheet
   - Presentations contain: DAU/MAU, ARPU, subscribers, GMV, take rate
   - Both are needed for complete analysis

## Next Steps

### Text Extraction
Use pdftotext (poppler-utils) to extract text from documents:
```bash
pdftotext NVDA_Presentation_Q3-FY2026.pdf
```

### Data Parsing
Parse extracted text to build metrics CSV and dashboard:
- Revenue by segment (Data Center, Gaming, Professional Visualization, Automotive)
- Gross margin, operating margin
- Customer concentration
- Geographic revenue breakdown
- Key operational metrics from presentations

### Dashboard Generation
Create interactive HTML dashboard showing:
- 10-year revenue trends
- Segment revenue breakdown
- Margin evolution
- Quarterly performance metrics
- Operational KPIs over time

## Download Scripts

### Python Scripts Created:
1. `download_nvda_filings_v2.py` - Downloads SEC 10-K and 10-Q filings
2. `download_nvda_presentations.py` - Downloads earnings presentations

Both scripts handle:
- Different naming conventions across years
- Automatic retry logic
- File size verification
- Progress reporting
