#!/usr/bin/env python3
"""
Extract Netflix financial data from 10-K and 10-Q files.
Uses targeted pattern matching on consolidated financial statements.
"""

import re
import csv
from pathlib import Path

EXTRACTED_DIR = Path("/Users/swilliams/Stocks/Research/NFLX/Extracted")
OUTPUT_CSV = Path("/Users/swilliams/Stocks/Research/NFLX/Reports/NFLX_Metrics.csv")
OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

def clean_num(s):
    """Clean and convert number string to float."""
    return float(s.replace(',', '').replace('$', '').replace('(', '-').replace(')', '').strip())

def find_table_value(lines, start_idx, search_term, is_eps=False):
    """Find a value in financial statement tables."""
    for i in range(start_idx, min(start_idx + 150, len(lines))):
        line = lines[i].strip()

        # Match the search term
        if re.match(f'^{re.escape(search_term)}\\s*$', line, re.IGNORECASE):
            # Found the row, now get the data
            if is_eps:
                # EPS has $ sign, look for $X.XX pattern in same or next lines
                for j in range(i, min(i+10, len(lines))):
                    match = re.search(r'\$\s*([\d.]+)', lines[j])
                    if match:
                        return float(match.group(1))
            else:
                # Regular number, usually on next line(s)
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if nums:
                        return clean_num(nums[0]) / 1000  # Convert thousands to millions
            break
    return None

def extract_10k(year):
    """Extract annual data from 10-K filing."""
    filepath = EXTRACTED_DIR / f'NFLX_10K_FY{year}.txt'
    if not filepath.exists():
        return None

    print(f"Extracting FY{year}...")

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    lines = content.split('\n')
    data = {'Period': f'FY{year}'}

    # Find sections
    income_idx = next((i for i, line in enumerate(lines) if 'CONSOLIDATED STATEMENTS OF OPERATIONS' in line), None)
    balance_idx = next((i for i, line in enumerate(lines) if 'CONSOLIDATED BALANCE SHEETS' in line), None)
    cashflow_idx = next((i for i, line in enumerate(lines) if 'CONSOLIDATED STATEMENTS OF CASH FLOWS' in line), None)

    # Extract from income statement
    if income_idx:
        data['Revenue'] = find_table_value(lines, income_idx, 'Revenues')
        data['CostOfRevenue'] = find_table_value(lines, income_idx, 'Cost of revenues')
        data['OperatingIncome'] = find_table_value(lines, income_idx, 'Operating income')
        data['NetIncome'] = find_table_value(lines, income_idx, 'Net income')
        data['EPSBasic'] = find_table_value(lines, income_idx, 'Basic', is_eps=True)
        data['EPSDiluted'] = find_table_value(lines, income_idx, 'Diluted', is_eps=True)

    # Extract from balance sheet
    if balance_idx:
        data['TotalAssets'] = find_table_value(lines, balance_idx, 'Total assets')
        data['TotalLiabilities'] = find_table_value(lines, balance_idx, 'Total liabilities')

        # Shareholders' equity - try various forms
        for term in ['Total stockholders\' equity', 'Total stockholders' equity', 'Total shareholders\' equity']:
            val = find_table_value(lines, balance_idx, term)
            if val:
                data['ShareholdersEquity'] = val
                break

        # Try to find long-term debt and cash
        for i in range(balance_idx, min(balance_idx + 200, len(lines))):
            line = lines[i].strip()
            if re.match(r'^Long-term debt\s*$', line, re.IGNORECASE):
                nums = re.findall(r'[\d,]+', line)
                if not nums and i+1 < len(lines):
                    nums = re.findall(r'[\d,]+', lines[i+1])
                if nums:
                    data['TotalDebt'] = clean_num(nums[0]) / 1000
                    break

        for i in range(balance_idx, min(balance_idx + 150, len(lines))):
            line = lines[i].strip()
            if re.match(r'^Cash and cash equivalents\s*$', line, re.IGNORECASE):
                nums = re.findall(r'[\d,]+', line)
                if not nums and i+1 < len(lines):
                    nums = re.findall(r'[\d,]+', lines[i+1])
                if nums:
                    data['CashAndEquivalents'] = clean_num(nums[0]) / 1000
                    break

    # Extract from cash flow
    if cashflow_idx:
        # Operating cash flow
        for i in range(cashflow_idx, min(cashflow_idx + 200, len(lines))):
            if 'Net cash provided by operating activities' in lines[i] or \
               'Net cash.*operating activities' in lines[i]:
                for j in range(i+1, min(i+10, len(lines))):
                    nums = re.findall(r'[\d,]+', lines[j])
                    if nums:
                        data['OperatingCashFlow'] = clean_num(nums[0]) / 1000
                        break
                break

        # CapEx
        for i in range(cashflow_idx, min(cashflow_idx + 200, len(lines))):
            if 'Purchases of property and equipment' in lines[i]:
                nums = re.findall(r'[\d,]+', lines[i])
                if nums:
                    data['CapEx'] = clean_num(nums[0]) / 1000
                    break

    # Find shares outstanding (diluted)
    for i, line in enumerate(lines):
        if 'Weighted-average' in line and ('shares' in line.lower() or 'common' in line.lower()):
            for j in range(i, min(i+25, len(lines))):
                if re.match(r'^\s*Diluted\s*$', lines[j]):
                    for k in range(j+1, min(j+10, len(lines))):
                        nums = re.findall(r'[\d,]+', lines[k])
                        if nums:
                            data['SharesOutstanding'] = clean_num(nums[0]) / 1000
                            break
                    break
            break

    # Find paid memberships
    for i, line in enumerate(lines):
        if 'Paid memberships at end of period' in line:
            for j in range(i+1, min(i+10, len(lines))):
                nums = re.findall(r'[\d,]+', lines[j])
                if nums:
                    data['PaidMemberships'] = clean_num(nums[0]) / 1000
                    break
            break

    # Calculate derived metrics
    if data.get('Revenue') and data.get('CostOfRevenue'):
        data['GrossProfit'] = data['Revenue'] - data['CostOfRevenue']
        data['GrossMargin'] = (data['GrossProfit'] / data['Revenue']) * 100

    if data.get('Revenue') and data.get('OperatingIncome'):
        data['OperatingMargin'] = (data['OperatingIncome'] / data['Revenue']) * 100

    if data.get('Revenue') and data.get('NetIncome'):
        data['NetMargin'] = (data['NetIncome'] / data['Revenue']) * 100

    if data.get('OperatingCashFlow') and data.get('CapEx'):
        data['FreeCashFlow'] = data['OperatingCashFlow'] - data['CapEx']

    return data

def extract_10q(quarter, year):
    """Extract quarterly data from 10-Q filing."""
    filepath = EXTRACTED_DIR / f'NFLX_10Q_Q{quarter}-{year}.txt'
    if not filepath.exists():
        return None

    print(f"Extracting Q{quarter} {year}...")

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    lines = content.split('\n')
    data = {'Period': f'Q{quarter} {year}'}

    # Find sections
    income_idx = next((i for i, line in enumerate(lines) if 'CONSOLIDATED STATEMENTS OF OPERATIONS' in line), None)
    balance_idx = next((i for i, line in enumerate(lines) if 'CONSOLIDATED BALANCE SHEETS' in line), None)

    # Extract from income statement (use first column - three months data)
    if income_idx:
        data['Revenue'] = find_table_value(lines, income_idx, 'Revenues')
        data['CostOfRevenue'] = find_table_value(lines, income_idx, 'Cost of revenues')
        data['OperatingIncome'] = find_table_value(lines, income_idx, 'Operating income')
        data['NetIncome'] = find_table_value(lines, income_idx, 'Net income')
        data['EPSBasic'] = find_table_value(lines, income_idx, 'Basic', is_eps=True)
        data['EPSDiluted'] = find_table_value(lines, income_idx, 'Diluted', is_eps=True)

    # Extract from balance sheet
    if balance_idx:
        data['TotalAssets'] = find_table_value(lines, balance_idx, 'Total assets')
        data['TotalLiabilities'] = find_table_value(lines, balance_idx, 'Total liabilities')

        for term in ['Total stockholders\' equity', 'Total stockholders' equity', 'Total shareholders\' equity']:
            val = find_table_value(lines, balance_idx, term)
            if val:
                data['ShareholdersEquity'] = val
                break

        for i in range(balance_idx, min(balance_idx + 200, len(lines))):
            line = lines[i].strip()
            if re.match(r'^Long-term debt\s*$', line, re.IGNORECASE):
                nums = re.findall(r'[\d,]+', line)
                if not nums and i+1 < len(lines):
                    nums = re.findall(r'[\d,]+', lines[i+1])
                if nums:
                    data['TotalDebt'] = clean_num(nums[0]) / 1000
                    break

        for i in range(balance_idx, min(balance_idx + 150, len(lines))):
            line = lines[i].strip()
            if re.match(r'^Cash and cash equivalents\s*$', line, re.IGNORECASE):
                nums = re.findall(r'[\d,]+', line)
                if not nums and i+1 < len(lines):
                    nums = re.findall(r'[\d,]+', lines[i+1])
                if nums:
                    data['CashAndEquivalents'] = clean_num(nums[0]) / 1000
                    break

    # Find shares outstanding
    for i, line in enumerate(lines):
        if 'Weighted-average' in line and ('shares' in line.lower() or 'common' in line.lower()):
            for j in range(i, min(i+25, len(lines))):
                if re.match(r'^\s*Diluted\s*$', lines[j]):
                    for k in range(j+1, min(j+10, len(lines))):
                        nums = re.findall(r'[\d,]+', lines[k])
                        if nums:
                            data['SharesOutstanding'] = clean_num(nums[0]) / 1000
                            break
                    break
            break

    # Find paid memberships
    for i, line in enumerate(lines):
        if 'Paid memberships at end of period' in line:
            for j in range(i+1, min(i+10, len(lines))):
                nums = re.findall(r'[\d,]+', lines[j])
                if nums:
                    data['PaidMemberships'] = clean_num(nums[0]) / 1000
                    break
            break

    # Calculate derived metrics
    if data.get('Revenue') and data.get('CostOfRevenue'):
        data['GrossProfit'] = data['Revenue'] - data['CostOfRevenue']
        data['GrossMargin'] = (data['GrossProfit'] / data['Revenue']) * 100

    if data.get('Revenue') and data.get('OperatingIncome'):
        data['OperatingMargin'] = (data['OperatingIncome'] / data['Revenue']) * 100

    if data.get('Revenue') and data.get('NetIncome'):
        data['NetMargin'] = (data['NetIncome'] / data['Revenue']) * 100

    return data

def main():
    all_data = []

    # Extract all 10-K files
    for year in range(2013, 2025):
        data = extract_10k(year)
        if data:
            all_data.append(data)

    # Extract all 10-Q files
    for year in range(2020, 2025):
        for quarter in range(1, 4):
            data = extract_10q(quarter, year)
            if data:
                all_data.append(data)

    # Sort chronologically
    def sort_key(item):
        period = item['Period']
        if period.startswith('FY'):
            return (int(period[2:]), 5)
        else:
            parts = period.split()
            return (int(parts[1]), int(parts[0][1]))

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
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for row in all_data:
            formatted = {col: '' for col in columns}
            formatted['Period'] = row['Period']

            for col in columns[1:]:
                if col in row and row[col] is not None:
                    val = row[col]
                    if col in ['GrossMargin', 'OperatingMargin', 'NetMargin']:
                        formatted[col] = f"{val:.1f}"
                    elif col in ['EPSBasic', 'EPSDiluted']:
                        formatted[col] = f"{val:.2f}"
                    else:
                        formatted[col] = f"{val:.1f}"

            writer.writerow(formatted)

    print(f"\n✓ Wrote {len(all_data)} periods to {OUTPUT_CSV}")

if __name__ == '__main__':
    main()
