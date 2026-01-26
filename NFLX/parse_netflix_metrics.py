#!/usr/bin/env python3
"""
Parse Netflix financial metrics from extracted 10-K and 10-Q files.
"""

import re
import csv
from pathlib import Path
from typing import Dict, List, Optional

# File paths
EXTRACTED_DIR = Path("/Users/swilliams/Stocks/Research/NFLX/Extracted")
OUTPUT_CSV = Path("/Users/swilliams/Stocks/Research/NFLX/Reports/NFLX_Metrics.csv")

# Create output directory if it doesn't exist
OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

def clean_number(text: str) -> Optional[float]:
    """Convert text number to float. Handles comma-separated numbers."""
    if not text:
        return None
    # Remove parentheses (indicating negative), commas, dollar signs, and whitespace
    cleaned = text.replace('(', '-').replace(')', '').replace(',', '').replace('$', '').strip()
    try:
        return float(cleaned)
    except (ValueError, AttributeError):
        return None

def extract_table_data(lines: List[str], start_idx: int, num_rows: int = 20) -> List[str]:
    """Extract a section of lines from the file."""
    return lines[start_idx:start_idx + num_rows]

def find_financial_statements(content: str) -> Dict[str, int]:
    """Find the positions of key financial statement sections."""
    lines = content.split('\n')
    positions = {}

    for i, line in enumerate(lines):
        if 'CONSOLIDATED STATEMENTS OF OPERATIONS' in line:
            positions['income_statement'] = i
        elif 'CONSOLIDATED BALANCE SHEETS' in line:
            positions['balance_sheet'] = i
        elif 'CONSOLIDATED STATEMENTS OF CASH FLOWS' in line:
            positions['cash_flow'] = i

    return positions, lines

def parse_10k_data(file_path: Path) -> Dict:
    """Parse data from a 10-K file."""
    year_match = re.search(r'FY(\d{4})', file_path.name)
    if not year_match:
        return None

    year = year_match.group(1)
    period = f"FY{year}"

    print(f"Parsing {file_path.name}...")

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    positions, lines = find_financial_statements(content)
    data = {'Period': period}

    # Extract from income statement (look for annual total)
    if 'income_statement' in positions:
        income_start = positions['income_statement']
        income_section = lines[income_start:income_start + 100]

        # Look for revenue, expenses, income patterns
        for i, line in enumerate(income_section):
            # Revenue
            if re.search(r'^\s*Revenues?\s*$', line, re.IGNORECASE):
                # Next line or nearby line should have the number
                for j in range(i+1, min(i+5, len(income_section))):
                    numbers = re.findall(r'[\d,]+', income_section[j])
                    if numbers and len(numbers) >= 1:
                        # Last year is typically the last or second-to-last number
                        data['Revenue'] = clean_number(numbers[-1])
                        break

            # Cost of revenues
            if re.search(r'^\s*Cost of revenues?\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(income_section))):
                    numbers = re.findall(r'[\d,]+', income_section[j])
                    if numbers and len(numbers) >= 1:
                        data['CostOfRevenue'] = clean_number(numbers[-1])
                        break

            # Operating income
            if re.search(r'^\s*Operating income\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(income_section))):
                    numbers = re.findall(r'[\d,]+', income_section[j])
                    if numbers:
                        data['OperatingIncome'] = clean_number(numbers[-1])
                        break

            # Net income
            if re.search(r'^\s*Net income\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(income_section))):
                    numbers = re.findall(r'[\d,]+', income_section[j])
                    if numbers:
                        data['NetIncome'] = clean_number(numbers[-1])
                        break

            # EPS basic and diluted
            if 'Earnings per share:' in line or 'basic' in line.lower():
                for j in range(i, min(i+10, len(income_section))):
                    if 'basic' in income_section[j].lower() and '$' in income_section[j]:
                        eps_match = re.search(r'\$\s*([\d.]+)', income_section[j])
                        if eps_match:
                            data['EPSBasic'] = float(eps_match.group(1))
                    if 'diluted' in income_section[j].lower() and '$' in income_section[j]:
                        eps_match = re.search(r'\$\s*([\d.]+)', income_section[j])
                        if eps_match:
                            data['EPSDiluted'] = float(eps_match.group(1))

    # Extract from balance sheet
    if 'balance_sheet' in positions:
        balance_start = positions['balance_sheet']
        balance_section = lines[balance_start:balance_start + 150]

        for i, line in enumerate(balance_section):
            if re.search(r'^\s*Total assets\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(balance_section))):
                    numbers = re.findall(r'[\d,]+', balance_section[j])
                    if numbers:
                        data['TotalAssets'] = clean_number(numbers[-1])
                        break

            if re.search(r'^\s*Total liabilities\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(balance_section))):
                    numbers = re.findall(r'[\d,]+', balance_section[j])
                    if numbers:
                        data['TotalLiabilities'] = clean_number(numbers[-1])
                        break

            if re.search(r'^\s*Total stockholders.*equity\s*$', line, re.IGNORECASE) or \
               re.search(r'^\s*Total shareholders.*equity\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(balance_section))):
                    numbers = re.findall(r'[\d,]+', balance_section[j])
                    if numbers:
                        data['ShareholdersEquity'] = clean_number(numbers[-1])
                        break

            if 'Long-term debt' in line and 'current' not in line.lower():
                numbers = re.findall(r'[\d,]+', line)
                if numbers:
                    data['TotalDebt'] = clean_number(numbers[-1])

            if 'Cash and cash equivalents' in line:
                numbers = re.findall(r'[\d,]+', line)
                if numbers:
                    data['CashAndEquivalents'] = clean_number(numbers[-1])

    # Extract from cash flow statement
    if 'cash_flow' in positions:
        cf_start = positions['cash_flow']
        cf_section = lines[cf_start:cf_start + 100]

        for i, line in enumerate(cf_section):
            if re.search(r'Net cash.*operating activities', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(cf_section))):
                    numbers = re.findall(r'[\d,]+', cf_section[j])
                    if numbers:
                        data['OperatingCashFlow'] = clean_number(numbers[-1])
                        break

            if 'Purchases of property and equipment' in line or 'Acquisition of property' in line:
                numbers = re.findall(r'\(?\s*[\d,]+\s*\)?', line)
                if numbers:
                    capex = clean_number(numbers[-1])
                    if capex:
                        data['CapEx'] = abs(capex)  # CapEx is usually negative in statement

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

    # Look for membership data in the text
    membership_patterns = [
        r'(\d+[\d,]*\.?\d*)\s*million.*paid memberships',
        r'paid memberships.*?(\d+[\d,]*\.?\d*)\s*million',
        r'ended with\s*(\d+[\d,]*\.?\d*)\s*million',
    ]

    for pattern in membership_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            # Get the last match (most recent)
            member_str = matches[-1].replace(',', '')
            try:
                data['PaidMemberships'] = float(member_str)
                break
            except:
                pass

    return data

def parse_10q_data(file_path: Path) -> Dict:
    """Parse data from a 10-Q file."""
    quarter_match = re.search(r'Q(\d)-(\d{4})', file_path.name)
    if not quarter_match:
        return None

    quarter = quarter_match.group(1)
    year = quarter_match.group(2)
    period = f"Q{quarter} {year}"

    print(f"Parsing {file_path.name}...")

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    positions, lines = find_financial_statements(content)
    data = {'Period': period}

    # Similar extraction as 10-K but look for quarterly data
    # 10-Q often shows "Three months ended" vs "Nine months ended"
    # We want the three months data

    if 'income_statement' in positions:
        income_start = positions['income_statement']
        income_section = lines[income_start:income_start + 100]

        # Find which column is "Three months ended"
        three_months_col = None
        for i, line in enumerate(income_section[:20]):
            if 'Three months ended' in line or 'three months ended' in line:
                # This line has the date headers
                # Try to identify which position
                three_months_col = 0  # Usually the first numeric column
                break

        for i, line in enumerate(income_section):
            if re.search(r'^\s*Revenues?\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(income_section))):
                    numbers = re.findall(r'[\d,]+', income_section[j])
                    if numbers:
                        # For Q data, typically first number is current quarter
                        data['Revenue'] = clean_number(numbers[0] if len(numbers) > 0 else numbers[-1])
                        break

            if re.search(r'^\s*Cost of revenues?\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(income_section))):
                    numbers = re.findall(r'[\d,]+', income_section[j])
                    if numbers:
                        data['CostOfRevenue'] = clean_number(numbers[0] if len(numbers) > 0 else numbers[-1])
                        break

            if re.search(r'^\s*Operating income\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(income_section))):
                    numbers = re.findall(r'[\d,]+', income_section[j])
                    if numbers:
                        data['OperatingIncome'] = clean_number(numbers[0] if len(numbers) > 0 else numbers[-1])
                        break

            if re.search(r'^\s*Net income\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(income_section))):
                    numbers = re.findall(r'[\d,]+', income_section[j])
                    if numbers:
                        data['NetIncome'] = clean_number(numbers[0] if len(numbers) > 0 else numbers[-1])
                        break

            if 'Earnings per share' in line or 'basic' in line.lower():
                for j in range(i, min(i+10, len(income_section))):
                    if 'basic' in income_section[j].lower() and '$' in income_section[j]:
                        eps_nums = re.findall(r'\$\s*([\d.]+)', income_section[j])
                        if eps_nums:
                            data['EPSBasic'] = float(eps_nums[0])
                    if 'diluted' in income_section[j].lower() and '$' in income_section[j]:
                        eps_nums = re.findall(r'\$\s*([\d.]+)', income_section[j])
                        if eps_nums:
                            data['EPSDiluted'] = float(eps_nums[0])

    # Balance sheet data (as of quarter end)
    if 'balance_sheet' in positions:
        balance_start = positions['balance_sheet']
        balance_section = lines[balance_start:balance_start + 150]

        for i, line in enumerate(balance_section):
            if re.search(r'^\s*Total assets\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(balance_section))):
                    numbers = re.findall(r'[\d,]+', balance_section[j])
                    if numbers:
                        data['TotalAssets'] = clean_number(numbers[0] if len(numbers) > 1 else numbers[-1])
                        break

            if re.search(r'^\s*Total liabilities\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(balance_section))):
                    numbers = re.findall(r'[\d,]+', balance_section[j])
                    if numbers:
                        data['TotalLiabilities'] = clean_number(numbers[0] if len(numbers) > 1 else numbers[-1])
                        break

            if re.search(r'^\s*Total stockholders.*equity\s*$', line, re.IGNORECASE):
                for j in range(i+1, min(i+5, len(balance_section))):
                    numbers = re.findall(r'[\d,]+', balance_section[j])
                    if numbers:
                        data['ShareholdersEquity'] = clean_number(numbers[0] if len(numbers) > 1 else numbers[-1])
                        break

            if 'Long-term debt' in line and 'current' not in line.lower():
                numbers = re.findall(r'[\d,]+', line)
                if numbers:
                    data['TotalDebt'] = clean_number(numbers[0] if len(numbers) > 1 else numbers[-1])

            if 'Cash and cash equivalents' in line:
                numbers = re.findall(r'[\d,]+', line)
                if numbers:
                    data['CashAndEquivalents'] = clean_number(numbers[0] if len(numbers) > 1 else numbers[-1])

    # Cash flow - note 10-Q shows YTD, would need to calculate quarter
    # For simplicity, we'll skip quarterly cash flow or use YTD

    # Calculate derived metrics
    if 'Revenue' in data and 'CostOfRevenue' in data:
        data['GrossProfit'] = data['Revenue'] - data['CostOfRevenue']
        data['GrossMargin'] = (data['GrossProfit'] / data['Revenue']) * 100

    if 'Revenue' in data and 'OperatingIncome' in data:
        data['OperatingMargin'] = (data['OperatingIncome'] / data['Revenue']) * 100

    if 'Revenue' in data and 'NetIncome' in data:
        data['NetMargin'] = (data['NetIncome'] / data['Revenue']) * 100

    # Look for membership data
    membership_patterns = [
        r'(\d+[\d,]*\.?\d*)\s*million.*paid memberships',
        r'paid memberships.*?(\d+[\d,]*\.?\d*)\s*million',
        r'ended with\s*(\d+[\d,]*\.?\d*)\s*million',
    ]

    for pattern in membership_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            member_str = matches[-1].replace(',', '')
            try:
                data['PaidMemberships'] = float(member_str)
                break
            except:
                pass

    return data

def main():
    """Main function to parse all files and generate CSV."""
    all_data = []

    # Parse all 10-K files (annual)
    for year in range(2013, 2025):
        file_path = EXTRACTED_DIR / f"NFLX_10K_FY{year}.txt"
        if file_path.exists():
            data = parse_10k_data(file_path)
            if data:
                all_data.append(data)

    # Parse all 10-Q files (quarterly) - Q1 2020 through Q3 2024
    for year in range(2020, 2025):
        for quarter in range(1, 4):  # Q1, Q2, Q3 (Q4 is in 10-K)
            file_path = EXTRACTED_DIR / f"NFLX_10Q_Q{quarter}-{year}.txt"
            if file_path.exists():
                data = parse_10q_data(file_path)
                if data:
                    all_data.append(data)

    # Sort data chronologically
    def sort_key(item):
        period = item['Period']
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 4, 0)  # FY goes at end of year
        else:
            # Q1 2020 format
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter, 0)

    all_data.sort(key=sort_key)

    # Define CSV columns
    columns = [
        'Period', 'Revenue', 'CostOfRevenue', 'GrossProfit', 'GrossMargin',
        'OperatingIncome', 'OperatingMargin', 'NetIncome', 'NetMargin',
        'EPSBasic', 'EPSDiluted', 'FreeCashFlow', 'OperatingCashFlow', 'CapEx',
        'TotalAssets', 'TotalLiabilities', 'ShareholdersEquity',
        'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding', 'PaidMemberships'
    ]

    # Write to CSV
    with open(OUTPUT_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()

        for row in all_data:
            # Format numbers to 1 decimal place for monetary values
            formatted_row = {}
            for col in columns:
                if col in row:
                    val = row[col]
                    if col == 'Period':
                        formatted_row[col] = val
                    elif val is not None:
                        if col in ['GrossMargin', 'OperatingMargin', 'NetMargin']:
                            formatted_row[col] = f"{val:.1f}"
                        elif col in ['EPSBasic', 'EPSDiluted']:
                            formatted_row[col] = f"{val:.2f}"
                        else:
                            formatted_row[col] = f"{val:.1f}"
                    else:
                        formatted_row[col] = ''
                else:
                    formatted_row[col] = ''

            writer.writerow(formatted_row)

    print(f"\nSuccessfully wrote {len(all_data)} rows to {OUTPUT_CSV}")
    print(f"\nFirst few rows:")
    for i, row in enumerate(all_data[:3]):
        print(f"  {row.get('Period', 'N/A')}: Revenue={row.get('Revenue', 'N/A')}, NetIncome={row.get('NetIncome', 'N/A')}")

if __name__ == '__main__':
    main()
