#!/usr/bin/env python3
"""
Manual extraction of Netflix financial metrics from 10-K and 10-Q files.
This script carefully parses the consolidated financial statements.
"""

import re
import csv
from pathlib import Path

EXTRACTED_DIR = Path("/Users/swilliams/Stocks/Research/NFLX/Extracted")
OUTPUT_CSV = Path("/Users/swilliams/Stocks/Research/NFLX/Reports/NFLX_Metrics.csv")

# Ensure output directory exists
OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

def read_file(filepath):
    """Read file content."""
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()

def extract_from_10k(filepath, year):
    """Extract metrics from 10-K file."""
    content = read_file(filepath)
    lines = content.split('\n')

    data = {'Period': f'FY{year}'}

    print(f"Processing FY{year}...")

    # Find consolidated statements of operations
    income_start = None
    for i, line in enumerate(lines):
        if 'CONSOLIDATED STATEMENTS OF OPERATIONS' in line:
            income_start = i
            break

    if income_start:
        # Look for the year column (most recent year is usually in first data column after headers)
        # Format: Year ended December 31, [year] [year-1] [year-2]
        # Revenues line followed by numbers
        for i in range(income_start, min(income_start + 150, len(lines))):
            line = lines[i]

            # Find Revenues
            if re.match(r'^\s*Revenues?\s*$', line):
                # Next few lines should have the numbers
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        # First number is most recent year
                        data['Revenue'] = float(nums[0].replace(',', '')) / 1000  # Convert from thousands to millions
                        break

            # Cost of revenues
            elif re.match(r'^\s*Cost of revenues?\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['CostOfRevenue'] = float(nums[0].replace(',', '')) / 1000
                        break

            # Operating income
            elif re.match(r'^\s*Operating income\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['OperatingIncome'] = float(nums[0].replace(',', '')) / 1000
                        break

            # Net income
            elif re.match(r'^\s*Net income\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['NetIncome'] = float(nums[0].replace(',', '')) / 1000
                        break

            # EPS Basic
            elif re.match(r'^\s*Basic\s*$', line):
                # Look for $ pattern
                nums = re.findall(r'\$\s*([\d.]+)', line)
                if not nums:
                    for j in range(i+1, min(i+5, len(lines))):
                        nums = re.findall(r'\$\s*([\d.]+)', lines[j])
                        if nums:
                            data['EPSBasic'] = float(nums[0])
                            break
                else:
                    data['EPSBasic'] = float(nums[0])

            # EPS Diluted
            elif re.match(r'^\s*Diluted\s*$', line):
                nums = re.findall(r'\$\s*([\d.]+)', line)
                if not nums:
                    for j in range(i+1, min(i+5, len(lines))):
                        nums = re.findall(r'\$\s*([\d.]+)', lines[j])
                        if nums:
                            data['EPSDiluted'] = float(nums[0])
                            break
                else:
                    data['EPSDiluted'] = float(nums[0])

    # Find balance sheet
    balance_start = None
    for i, line in enumerate(lines):
        if 'CONSOLIDATED BALANCE SHEETS' in line:
            balance_start = i
            break

    if balance_start:
        for i in range(balance_start, min(balance_start + 200, len(lines))):
            line = lines[i]

            # Total assets
            if re.match(r'^\s*Total assets\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['TotalAssets'] = float(nums[0].replace(',', '')) / 1000
                        break

            # Total liabilities
            elif re.match(r'^\s*Total liabilities\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['TotalLiabilities'] = float(nums[0].replace(',', '')) / 1000
                        break

            # Stockholders' equity
            elif 'Total stockholders' in line and 'equity' in line.lower():
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['ShareholdersEquity'] = float(nums[0].replace(',', '')) / 1000
                        break

            # Long-term debt
            elif re.match(r'^\s*Long-term debt\s*$', line):
                nums = re.findall(r'[\d,]+', line)
                if len(nums) >= 1:
                    data['TotalDebt'] = float(nums[0].replace(',', '')) / 1000

            # Cash and cash equivalents
            elif re.match(r'^\s*Cash and cash equivalents\s*$', line):
                nums = re.findall(r'[\d,]+', line)
                if len(nums) >= 1:
                    data['CashAndEquivalents'] = float(nums[0].replace(',', '')) / 1000

    # Find cash flow statement
    cf_start = None
    for i, line in enumerate(lines):
        if 'CONSOLIDATED STATEMENTS OF CASH FLOWS' in line:
            cf_start = i
            break

    if cf_start:
        for i in range(cf_start, min(cf_start + 200, len(lines))):
            line = lines[i]

            # Net cash provided by operating activities
            if 'Net cash provided by operating activities' in line or \
               (re.match(r'^\s*Net cash.*operating activities\s*$', line)):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['OperatingCashFlow'] = float(nums[0].replace(',', '')) / 1000
                        break

            # Purchases of property and equipment (CapEx)
            elif 'Purchases of property and equipment' in line:
                nums = re.findall(r'[\d,]+', line)
                if len(nums) >= 1:
                    data['CapEx'] = float(nums[0].replace(',', '')) / 1000

    # Find shares outstanding (diluted weighted average)
    for i, line in enumerate(lines):
        if 'Weighted-average shares' in line or 'Weighted-average common' in line:
            # Look for Diluted line
            for j in range(i, min(i+20, len(lines))):
                if re.match(r'^\s*Diluted\s*$', lines[j]):
                    for k in range(j+1, min(j+10, len(lines))):
                        nums = re.findall(r'[\d,]+', lines[k])
                        if len(nums) >= 1:
                            data['SharesOutstanding'] = float(nums[0].replace(',', '')) / 1000
                            break
                    break

    # Find paid memberships
    # Look for "Paid memberships at end of period" with numbers in thousands
    for i, line in enumerate(lines):
        if 'Paid memberships at end of period' in line:
            for j in range(i+1, min(i+10, len(lines))):
                nums = re.findall(r'[\d,]+', lines[j])
                if len(nums) >= 1:
                    # Number is in thousands, so convert to millions
                    data['PaidMemberships'] = float(nums[0].replace(',', '')) / 1000
                    break
            break

    # Calculate derived metrics
    if 'Revenue' in data and 'CostOfRevenue' in data:
        data['GrossProfit'] = data['Revenue'] - data['CostOfRevenue']
        data['GrossMargin'] = (data['GrossProfit'] / data['Revenue']) * 100

    if 'Revenue' in data and 'OperatingIncome' in data:
        data['OperatingMargin'] = (data['OperatingIncome'] / data['Revenue']) * 100

    if 'Revenue' in data and 'NetIncome' in data:
        data['NetMargin'] = (data['NetIncome'] / data['Revenue']) * 100

    if 'OperatingCashFlow' in data and 'CapEx' in data:
        data['FreeCashFlow'] = data['OperatingCashFlow'] - data['CapEx']

    return data

def extract_from_10q(filepath, quarter, year):
    """Extract metrics from 10-Q file."""
    content = read_file(filepath)
    lines = content.split('\n')

    data = {'Period': f'Q{quarter} {year}'}

    print(f"Processing Q{quarter} {year}...")

    # Similar to 10-K but looking for "Three months ended" column
    income_start = None
    for i, line in enumerate(lines):
        if 'CONSOLIDATED STATEMENTS OF OPERATIONS' in line:
            income_start = i
            break

    if income_start:
        # In 10-Q, look for "Three months ended" to identify the right column
        # Usually the first numeric column after headers
        for i in range(income_start, min(income_start + 150, len(lines))):
            line = lines[i]

            if re.match(r'^\s*Revenues?\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['Revenue'] = float(nums[0].replace(',', '')) / 1000
                        break

            elif re.match(r'^\s*Cost of revenues?\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['CostOfRevenue'] = float(nums[0].replace(',', '')) / 1000
                        break

            elif re.match(r'^\s*Operating income\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['OperatingIncome'] = float(nums[0].replace(',', '')) / 1000
                        break

            elif re.match(r'^\s*Net income\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['NetIncome'] = float(nums[0].replace(',', '')) / 1000
                        break

            elif re.match(r'^\s*Basic\s*$', line):
                nums = re.findall(r'\$\s*([\d.]+)', line)
                if not nums:
                    for j in range(i+1, min(i+5, len(lines))):
                        nums = re.findall(r'\$\s*([\d.]+)', lines[j])
                        if nums:
                            data['EPSBasic'] = float(nums[0])
                            break
                else:
                    data['EPSBasic'] = float(nums[0])

            elif re.match(r'^\s*Diluted\s*$', line):
                nums = re.findall(r'\$\s*([\d.]+)', line)
                if not nums:
                    for j in range(i+1, min(i+5, len(lines))):
                        nums = re.findall(r'\$\s*([\d.]+)', lines[j])
                        if nums:
                            data['EPSDiluted'] = float(nums[0])
                            break
                else:
                    data['EPSDiluted'] = float(nums[0])

    # Balance sheet (as of quarter end - use first column which is most recent)
    balance_start = None
    for i, line in enumerate(lines):
        if 'CONSOLIDATED BALANCE SHEETS' in line:
            balance_start = i
            break

    if balance_start:
        for i in range(balance_start, min(balance_start + 200, len(lines))):
            line = lines[i]

            if re.match(r'^\s*Total assets\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['TotalAssets'] = float(nums[0].replace(',', '')) / 1000
                        break

            elif re.match(r'^\s*Total liabilities\s*$', line):
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['TotalLiabilities'] = float(nums[0].replace(',', '')) / 1000
                        break

            elif 'Total stockholders' in line and 'equity' in line.lower():
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if len(nums) >= 1:
                        data['ShareholdersEquity'] = float(nums[0].replace(',', '')) / 1000
                        break

            elif re.match(r'^\s*Long-term debt\s*$', line):
                nums = re.findall(r'[\d,]+', line)
                if len(nums) >= 1:
                    data['TotalDebt'] = float(nums[0].replace(',', '')) / 1000

            elif re.match(r'^\s*Cash and cash equivalents\s*$', line):
                nums = re.findall(r'[\d,]+', line)
                if len(nums) >= 1:
                    data['CashAndEquivalents'] = float(nums[0].replace(',', '')) / 1000

    # Find shares outstanding
    for i, line in enumerate(lines):
        if 'Weighted-average shares' in line or 'Weighted-average common' in line:
            for j in range(i, min(i+20, len(lines))):
                if re.match(r'^\s*Diluted\s*$', lines[j]):
                    for k in range(j+1, min(j+10, len(lines))):
                        nums = re.findall(r'[\d,]+', lines[k])
                        if len(nums) >= 1:
                            data['SharesOutstanding'] = float(nums[0].replace(',', '')) / 1000
                            break
                    break

    # Find paid memberships
    for i, line in enumerate(lines):
        if 'Paid memberships at end of period' in line:
            for j in range(i+1, min(i+10, len(lines))):
                nums = re.findall(r'[\d,]+', lines[j])
                if len(nums) >= 1:
                    data['PaidMemberships'] = float(nums[0].replace(',', '')) / 1000
                    break
            break

    # Calculate derived metrics
    if 'Revenue' in data and 'CostOfRevenue' in data:
        data['GrossProfit'] = data['Revenue'] - data['CostOfRevenue']
        data['GrossMargin'] = (data['GrossProfit'] / data['Revenue']) * 100

    if 'Revenue' in data and 'OperatingIncome' in data:
        data['OperatingMargin'] = (data['OperatingIncome'] / data['Revenue']) * 100

    if 'Revenue' in data and 'NetIncome' in data:
        data['NetMargin'] = (data['NetIncome'] / data['Revenue']) * 100

    return data

def main():
    all_data = []

    # Process all 10-K files
    for year in range(2013, 2025):
        filepath = EXTRACTED_DIR / f'NFLX_10K_FY{year}.txt'
        if filepath.exists():
            data = extract_from_10k(filepath, year)
            all_data.append(data)

    # Process all 10-Q files
    for year in range(2020, 2025):
        for quarter in range(1, 4):  # Q1, Q2, Q3
            filepath = EXTRACTED_DIR / f'NFLX_10Q_Q{quarter}-{year}.txt'
            if filepath.exists():
                data = extract_from_10q(filepath, quarter, year)
                all_data.append(data)

    # Sort chronologically
    def sort_key(item):
        period = item['Period']
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 5)  # FY at end
        else:
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter)

    all_data.sort(key=sort_key)

    # Write CSV
    columns = [
        'Period', 'Revenue', 'CostOfRevenue', 'GrossProfit', 'GrossMargin',
        'OperatingIncome', 'OperatingMargin', 'NetIncome', 'NetMargin',
        'EPSBasic', 'EPSDiluted', 'FreeCashFlow', 'OperatingCashFlow', 'CapEx',
        'TotalAssets', 'TotalLiabilities', 'ShareholdersEquity',
        'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding', 'PaidMemberships'
    ]

    with open(OUTPUT_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()

        for row in all_data:
            formatted_row = {}
            for col in columns:
                if col in row and row[col] is not None:
                    if col == 'Period':
                        formatted_row[col] = row[col]
                    elif col in ['GrossMargin', 'OperatingMargin', 'NetMargin']:
                        formatted_row[col] = f"{row[col]:.1f}"
                    elif col in ['EPSBasic', 'EPSDiluted']:
                        formatted_row[col] = f"{row[col]:.2f}"
                    else:
                        formatted_row[col] = f"{row[col]:.1f}"
                else:
                    formatted_row[col] = ''
            writer.writerow(formatted_row)

    print(f"\n✓ Successfully wrote {len(all_data)} periods to {OUTPUT_CSV}")
    print(f"\nSample data:")
    for row in all_data[-3:]:
        print(f"  {row['Period']}: Rev={row.get('Revenue', 'N/A'):.1f}M, NetInc={row.get('NetIncome', 'N/A'):.1f}M, Members={row.get('PaidMemberships', 'N/A'):.1f}M")

if __name__ == '__main__':
    main()
