#!/usr/bin/env python3
"""
Build complete MSFT metrics CSV with quarterly and annual data
"""
import re
import csv
from pathlib import Path

EXTRACTED_DIR = Path("/Users/swilliams/Stocks/Research/MSFT/Extracted")
OUTPUT_CSV = Path("/Users/swilliams/Stocks/Research/MSFT/Reports/MSFT_Metrics.csv")

# Define all periods in chronological order
QUARTERS = []
for fy in range(2020, 2026):
    for q in ['Q1', 'Q2', 'Q3']:
        QUARTERS.append(f"{q} FY{fy}")
# Add Q2 FY2026
QUARTERS.append('Q1 FY2026')
QUARTERS.append('Q2 FY2026')

ANNUAL_YEARS = [f"FY{y}" for y in range(2015, 2026)]

def extract_quarterly_data(file_path):
    """Extract metrics from 10-Q file"""
    with open(file_path, 'r') as f:
        content = f.read()

    data = {}

    # Extract from SEGMENT RESULTS section - most reliable
    segment_section = re.search(
        r'SEGMENT RESULTS OF OPERATIONS.*?'
        r'Productivity and Business Processes.*?Revenue.*?\$\s*([\d,]+).*?'
        r'Intelligent Cloud.*?Revenue.*?\$\s*([\d,]+).*?'
        r'More Personal Computing.*?Revenue.*?\$\s*([\d,]+).*?'
        r'Total.*?Revenue.*?\$\s*([\d,]+)',
        content, re.DOTALL
    )

    if segment_section:
        data['ProductivityRevenue'] = int(segment_section.group(1).replace(',', ''))
        data['CloudRevenue'] = int(segment_section.group(2).replace(',', ''))
        data['PersonalComputingRevenue'] = int(segment_section.group(3).replace(',', ''))
        data['Revenue'] = int(segment_section.group(4).replace(',', ''))

    # Extract from income statement
    income_section = re.search(
        r'INCOME STATEMENTS.*?Three Months Ended.*?\n(.*?)\nRefer to accompanying notes',
        content, re.DOTALL
    )

    if income_section:
        inc_text = income_section.group(1)

        # Total revenue
        rev_match = re.search(r'Total revenue\s*\n\s*([\d,]+)', inc_text)
        if rev_match and 'Revenue' not in data:
            data['Revenue'] = int(rev_match.group(1).replace(',', ''))

        # Gross margin
        gm_match = re.search(r'Gross margin\s*\n\s*([\d,]+)', inc_text)
        if gm_match:
            data['GrossProfit'] = int(gm_match.group(1).replace(',', ''))

        # Operating income
        oi_match = re.search(r'Operating income\s*\n\s*([\d,]+)', inc_text)
        if oi_match:
            data['OperatingIncome'] = int(oi_match.group(1).replace(',', ''))

        # Net income - more complex pattern
        ni_match = re.search(r'Net income\s*\n\s*\$\s*\n\s*([\d,]+)', inc_text)
        if ni_match:
            data['NetIncome'] = int(ni_match.group(1).replace(',', ''))

        # EPS
        eps_match = re.search(r'Diluted\s*\n\s*\$\s*\n\s*([\d.]+)\s*\n\s*\$', inc_text)
        if eps_match:
            data['EPS'] = float(eps_match.group(1))

    # Calculate margins
    if 'Revenue' in data and 'GrossProfit' in data:
        data['GrossMargin'] = round(data['GrossProfit'] / data['Revenue'] * 100, 1)
    if 'Revenue' in data and 'OperatingIncome' in data:
        data['OperatingMargin'] = round(data['OperatingIncome'] / data['Revenue'] * 100, 1)
    if 'Revenue' in data and 'NetIncome' in data:
        data['NetMargin'] = round(data['NetIncome'] / data['Revenue'] * 100, 1)

    return data

def extract_annual_data(file_path):
    """Extract metrics from 10-K file"""
    with open(file_path, 'r') as f:
        content = f.read()

    data = {}

    # Extract from SEGMENT RESULTS section
    segment_section = re.search(
        r'SEGMENT RESULTS OF OPERATIONS.*?'
        r'Productivity and Business Processes.*?Revenue.*?\$\s*([\d,]+).*?'
        r'Intelligent Cloud.*?Revenue.*?\$\s*([\d,]+).*?'
        r'More Personal Computing.*?Revenue.*?\$\s*([\d,]+).*?'
        r'Total.*?Revenue.*?\$\s*([\d,]+)',
        content, re.DOTALL
    )

    if segment_section:
        data['ProductivityRevenue'] = int(segment_section.group(1).replace(',', ''))
        data['CloudRevenue'] = int(segment_section.group(2).replace(',', ''))
        data['PersonalComputingRevenue'] = int(segment_section.group(3).replace(',', ''))
        data['Revenue'] = int(segment_section.group(4).replace(',', ''))

    # Extract from CONSOLIDATED INCOME STATEMENTS
    income_section = re.search(
        r'CONSOLIDATED INCOME STATEMENTS.*?Year Ended June 30.*?\n(.*?)See accompanying notes',
        content, re.DOTALL
    )

    if income_section:
        inc_text = income_section.group(1)

        # Total revenue
        rev_match = re.search(r'Total revenue\s*\n\s*([\d,]+)', inc_text)
        if rev_match and 'Revenue' not in data:
            data['Revenue'] = int(rev_match.group(1).replace(',', ''))

        # Gross margin
        gm_match = re.search(r'Gross margin\s*\n\s*([\d,]+)', inc_text)
        if gm_match:
            data['GrossProfit'] = int(gm_match.group(1).replace(',', ''))

        # Operating income
        oi_match = re.search(r'Operating income\s*\n\s*([\d,]+)', inc_text)
        if oi_match:
            data['OperatingIncome'] = int(oi_match.group(1).replace(',', ''))

        # Net income
        ni_match = re.search(r'Net income\s*\n\s*\$\s*\n\s*([\d,]+)', inc_text)
        if ni_match:
            data['NetIncome'] = int(ni_match.group(1).replace(',', ''))

        # EPS
        eps_match = re.search(r'Diluted\s*\n\s*\$\s*\n\s*([\d.]+)\s*\n', inc_text)
        if eps_match:
            data['EPS'] = float(eps_match.group(1))

    # Extract from cash flow statement
    cf_section = re.search(
        r'CONSOLIDATED CASH FLOWS STATEMENTS.*?Year Ended June 30.*?\n(.*?)CONSOLIDATED BALANCE SHEETS',
        content, re.DOTALL
    )

    if cf_section:
        cf_text = cf_section.group(1)

        # Operating cash flow
        ocf_match = re.search(r'Net cash from operations\s*\n\s*\$?\s*([\d,]+)', cf_text)
        if ocf_match:
            data['OperatingCashFlow'] = int(ocf_match.group(1).replace(',', ''))

        # CapEx
        capex_match = re.search(r'Additions to property and equipment\s*\n\s*\(\s*([\d,]+)', cf_text)
        if capex_match:
            data['CapEx'] = int(capex_match.group(1).replace(',', ''))

    # Calculate FCF
    if 'OperatingCashFlow' in data and 'CapEx' in data:
        data['FreeCashFlow'] = data['OperatingCashFlow'] - data['CapEx']

    # Extract from balance sheet
    bs_section = re.search(
        r'CONSOLIDATED BALANCE SHEETS.*?June 30.*?\n(.*?)CONSOLIDATED CASH FLOWS',
        content, re.DOTALL
    )

    if bs_section:
        bs_text = bs_section.group(1)

        # Shareholders' equity
        equity_match = re.search(r'Total stockholders\' equity\s*\n\s*\$?\s*([\d,]+)', bs_text)
        if equity_match:
            data['ShareholdersEquity'] = int(equity_match.group(1).replace(',', ''))

        # Cash and equivalents
        cash_match = re.search(r'Cash and cash equivalents\s*\n\s*\$?\s*([\d,]+)', bs_text)
        if cash_match:
            data['CashAndEquivalents'] = int(cash_match.group(1).replace(',', ''))

        # Current portion of debt
        current_debt = 0
        cd_match = re.search(r'Current portion of long-term debt\s*\n\s*\$?\s*([\d,]+)', bs_text)
        if cd_match:
            current_debt = int(cd_match.group(1).replace(',', ''))

        # Long-term debt
        ltd_match = re.search(r'Long-term debt\s*\n\s*\$?\s*([\d,]+)', bs_text)
        if ltd_match:
            longterm_debt = int(ltd_match.group(1).replace(',', ''))
            data['TotalDebt'] = current_debt + longterm_debt

    # Calculate margins
    if 'Revenue' in data and 'GrossProfit' in data:
        data['GrossMargin'] = round(data['GrossProfit'] / data['Revenue'] * 100, 1)
    if 'Revenue' in data and 'OperatingIncome' in data:
        data['OperatingMargin'] = round(data['OperatingIncome'] / data['Revenue'] * 100, 1)
    if 'Revenue' in data and 'NetIncome' in data:
        data['NetMargin'] = round(data['NetIncome'] / data['Revenue'] * 100, 1)

    return data

# Build complete dataset
all_data = {}

# Process quarterly data
for period in QUARTERS:
    # Convert period to filename
    q_num = period.split()[0]  # Q1, Q2, Q3
    fy = period.split()[1]     # FY2020, etc

    file_path = EXTRACTED_DIR / f"MSFT_10Q_{q_num}-{fy}.txt"

    if file_path.exists():
        print(f"Processing {period}...")
        data = extract_quarterly_data(file_path)
        if data:
            all_data[period] = data
        else:
            print(f"  WARNING: No data extracted for {period}")
    else:
        print(f"  WARNING: File not found for {period}")

# Process annual data
for year in ANNUAL_YEARS:
    file_path = EXTRACTED_DIR / f"MSFT_10K_{year}.txt"

    if file_path.exists():
        print(f"Processing {year}...")
        data = extract_annual_data(file_path)
        if data:
            all_data[year] = data
        else:
            print(f"  WARNING: No data extracted for {year}")
    else:
        print(f"  WARNING: File not found for {year}")

# Write CSV
print(f"\nWriting CSV with {len(all_data)} periods...")

# Define column order
columns = [
    'Period', 'Revenue', 'GrossProfit', 'GrossMargin', 'OperatingIncome', 'OperatingMargin',
    'NetIncome', 'NetMargin', 'EPS', 'FreeCashFlow', 'OperatingCashFlow', 'CapEx',
    'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding',
    'CloudRevenue', 'ProductivityRevenue', 'PersonalComputingRevenue'
]

# Create chronological order: FY2015, FY2016, ..., Q1 FY2020, Q2 FY2020, ..., FY2020, Q1 FY2021, ...
ordered_periods = []
for year in range(2015, 2026):
    fy_str = f"FY{year}"

    # Add quarters for FY2020+
    if year >= 2020:
        for q in ['Q1', 'Q2', 'Q3']:
            period = f"{q} FY{year}"
            if period in all_data:
                ordered_periods.append(period)

    # Add annual
    if fy_str in all_data:
        ordered_periods.append(fy_str)

# Add FY2026 quarters
for q in ['Q1', 'Q2']:
    period = f"{q} FY2026"
    if period in all_data:
        ordered_periods.append(period)

with open(OUTPUT_CSV, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=columns)
    writer.writeheader()

    for period in ordered_periods:
        if period in all_data:
            row = {'Period': period}
            row.update(all_data[period])
            writer.writerow(row)

print(f"Complete! Wrote {len(ordered_periods)} periods to {OUTPUT_CSV}")
print("\nSample data:")
for period in ordered_periods[:5]:
    print(f"  {period}: Revenue={all_data[period].get('Revenue', 'N/A')}")
