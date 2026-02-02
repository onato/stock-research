# Fiserv (FISV) SEC Filings Download Summary

## Download Date
2026-02-02

## Successfully Downloaded: 42 Files

### 10-K Annual Reports: 11 files
Fiscal years: FY2014 through FY2024 (11 consecutive years)

Files:
- FISV_10K_FY2024.htm (3.7M) - Period ending 2024-12-31
- FISV_10K_FY2023.htm (2.7M) - Period ending 2023-12-31
- FISV_10K_FY2022.htm (3.7M) - Period ending 2022-12-31
- FISV_10K_FY2021.htm (4.1M) - Period ending 2021-12-31
- FISV_10K_FY2020.htm (3.8M) - Period ending 2020-12-31
- FISV_10K_FY2019.htm (4.7M) - Period ending 2019-12-31
- FISV_10K_FY2018.htm (2.2M) - Period ending 2018-12-31
- FISV_10K_FY2017.htm (1.8M) - Period ending 2017-12-31
- FISV_10K_FY2016.htm (1.7M) - Period ending 2016-12-31
- FISV_10K_FY2015.htm (1.7M) - Period ending 2015-12-31
- FISV_10K_FY2014.htm (1.4M) - Period ending 2014-12-31

### 10-Q Quarterly Reports: 31 files
Coverage: Q3-2015 through Q3-2025 (10+ years of quarterly data)

**2025 (3 quarters):**
- FISV_10Q_Q1-2025.htm (1.3M)
- FISV_10Q_Q2-2025.htm (1.7M)
- FISV_10Q_Q3-2025.htm (1.7M)

**2024 (3 quarters):**
- FISV_10Q_Q1-2024.htm (1.2M)
- FISV_10Q_Q2-2024.htm (2.4M)
- FISV_10Q_Q3-2024.htm (1.6M)

**2023 (3 quarters):**
- FISV_10Q_Q1-2023.htm (1.7M)
- FISV_10Q_Q2-2023.htm (1.8M)
- FISV_10Q_Q3-2023.htm (1.8M)

**2022 (3 quarters):**
- FISV_10Q_Q1-2022.htm (2.1M)
- FISV_10Q_Q2-2022.htm (2.0M)
- FISV_10Q_Q3-2022.htm (2.1M)

**2021 (3 quarters):**
- FISV_10Q_Q1-2021.htm (2.1M)
- FISV_10Q_Q2-2021.htm (1.8M)
- FISV_10Q_Q3-2021.htm (2.1M)

**2020 (3 quarters):**
- FISV_10Q_Q1-2020.htm (1.8M)
- FISV_10Q_Q2-2020.htm (2.6M)
- FISV_10Q_Q3-2020.htm (3.1M)

**2019 (3 quarters):**
- FISV_10Q_Q1-2019.htm (1.0M)
- FISV_10Q_Q2-2019.htm (2.0M)
- FISV_10Q_Q3-2019.htm (2.9M)

**2018 (3 quarters):**
- FISV_10Q_Q1-2018.htm (1.0M)
- FISV_10Q_Q2-2018.htm (2.1M)
- FISV_10Q_Q3-2018.htm (2.1M)

**2017 (3 quarters):**
- FISV_10Q_Q1-2017.htm (640K)
- FISV_10Q_Q2-2017.htm (896K)
- FISV_10Q_Q3-2017.htm (896K)

**2016 (3 quarters):**
- FISV_10Q_Q1-2016.htm (640K)
- FISV_10Q_Q2-2016.htm (832K)
- FISV_10Q_Q3-2016.htm (832K)

**2015 (1 quarter):**
- FISV_10Q_Q3-2015.htm (832K)

## Missing Files: 4 files

These files could not be downloaded due to SEC access restrictions (HTTP 403):

### 10-K Annual Reports: 2 files
- FY2012 (accession: 0001193125-13-071589)
- FY2013 (accession: 0001193125-14-060861)

### 10-Q Quarterly Reports: 2 files
- Q1-2015 (accession: 0001193125-15-174385)
- Q2-2015 (accession: 0001193125-15-270747)

**Note:** These older filings (2012-2015) appear to have access restrictions on the SEC EDGAR system. They can be viewed through the SEC's interactive data viewer but not downloaded directly as HTML files.

## File Format
All files are in HTML/iXBRL format (.htm extension), which can be:
1. Viewed directly in a web browser
2. Converted to text using pdftotext or similar tools
3. Parsed for financial data extraction

## Company Information
- **Ticker:** FISV (formerly), now FI
- **CIK:** 0000798354
- **Company Name:** Fiserv, Inc.
- **SEC EDGAR Page:** https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000798354

## Data Coverage Summary
- **10-K Coverage:** 11 years (FY2014-FY2024)
- **10-Q Coverage:** ~10 years (Q3-2015 through Q3-2025)
- **Total Periods:** 42 reporting periods
- **Estimated Total Size:** ~75-80 MB

## Next Steps
These files can now be:
1. Converted to plain text for analysis
2. Parsed to extract financial metrics
3. Used to build a financial dashboard
4. Analyzed for revenue trends, profitability, segment performance, etc.

## Download Scripts
Three Python scripts were created during this process:
1. `download_fisv_filings.py` - Initial download script
2. `download_fisv_filings_v2.py` - Older filings with different naming
3. `download_fisv_all.py` - Comprehensive download script (final version)
4. `download_fisv_missing.py` - Script for missing files
