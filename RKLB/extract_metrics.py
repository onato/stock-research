#!/usr/bin/env python3
"""
Extract financial metrics from Rocket Lab SEC filings.
"""

import re
import csv
from pathlib import Path
from collections import defaultdict

# Define the data structure
metrics = []

def clean_number(text):
    """Convert text like '155,080' or '(145,287)' to float."""
    if not text:
        return None
    # Remove commas and whitespace
    text = text.replace(',', '').strip()
    # Handle parentheses (negative numbers)
    if '(' in text and ')' in text:
        text = text.replace('(', '').replace(')', '')
        return -float(text)
    try:
        return float(text)
    except:
        return None

def extract_from_file(filepath, period):
    """Extract financial metrics from a single filing."""
    print(f"Processing {filepath.name} for period {period}...")

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    data = {'Period': period}

    # Find statements of operations
    # Look for quarterly data (Three Months Ended pattern)
    ops_match = re.search(
        r'CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS.*?'
        r'Three Months Ended.*?'
        r'(\d{4})\s*(\d{4})\s*'
        r'Revenues:.*?'
        r'Total revenues\s*\$?\s*([\d,]+).*?'
        r'Total cost of revenues\s*\$?\s*([\d,]+).*?'
        r'Gross profit\s*\$?\s*([\d,]+).*?'
        r'Operating (?:loss|income)\s*\$?\s*\(?\s*([\d,]+)\)?',
        content, re.DOTALL
    )

    if ops_match:
        # Get the first column (most recent quarter)
        revenue = clean_number(ops_match.group(3))
        gross_profit = clean_number(ops_match.group(5))
        operating_income = clean_number(ops_match.group(6))

        # Operating income is negative (loss)
        if operating_income and operating_income > 0:
            operating_income = -operating_income

        data['Revenue'] = revenue / 1000 if revenue else None  # Convert to millions
        data['GrossProfit'] = gross_profit / 1000 if gross_profit else None
        data['GrossMargin'] = (gross_profit / revenue * 100) if (revenue and gross_profit) else None
        data['OperatingIncome'] = operating_income / 1000 if operating_income else None
        data['OperatingMargin'] = (operating_income / revenue * 100) if (revenue and operating_income) else None

    # Look for net loss and EPS
    net_loss_match = re.search(
        r'Net loss\s*\$\s*\(\s*([\d,]+)\s*\).*?'
        r'Net loss per share.*?Basic and diluted\s*\$\s*\(\s*([\d.]+)\s*\)',
        content, re.DOTALL
    )

    if net_loss_match:
        net_loss = clean_number(f"({net_loss_match.group(1)})")
        eps = clean_number(f"({net_loss_match.group(2)})")

        data['NetIncome'] = net_loss / 1000 if net_loss else None
        data['NetMargin'] = (net_loss / data.get('Revenue', 1) / 1000 * 100) if (net_loss and data.get('Revenue')) else None
        data['EPS'] = eps

    # Look for balance sheet data
    balance_match = re.search(
        r'Total assets\s*\$\s*([\d,]+).*?'
        r'Total liabilities\s*([\d,]+).*?'
        r'Total stockholders\' equity\s*([\d,]+)',
        content, re.DOTALL
    )

    if balance_match:
        data['TotalAssets'] = clean_number(balance_match.group(1)) / 1000
        data['TotalLiabilities'] = clean_number(balance_match.group(2)) / 1000
        data['ShareholdersEquity'] = clean_number(balance_match.group(3)) / 1000

    # Look for cash and cash equivalents
    cash_match = re.search(r'Cash and cash equivalents\s*\$\s*([\d,]+)', content)
    if cash_match:
        data['CashAndEquivalents'] = clean_number(cash_match.group(1)) / 1000

    # Look for marketable securities to add to cash
    securities_match = re.search(r'Marketable securities, current\s*\$?\s*([\d,]+)', content)
    if securities_match and data.get('CashAndEquivalents'):
        securities = clean_number(securities_match.group(1)) / 1000
        data['CashAndEquivalents'] += securities

    # Look for shares outstanding (diluted)
    shares_match = re.search(
        r'Weighted-average common shares outstanding:.*?'
        r'Basic and diluted\s*([\d,]+)',
        content, re.DOTALL
    )
    if shares_match:
        data['SharesOutstanding'] = clean_number(shares_match.group(1)) / 1000000  # Convert to millions

    # Look for segment revenue (Launch Services and Space Systems)
    segment_match = re.search(
        r'Three Months Ended.*?'
        r'Launch\s*Services\s*Space\s*Systems.*?'
        r'Revenues\s*\$\s*([\d,]+)\s*\$\s*([\d,]+)',
        content, re.DOTALL
    )
    if segment_match:
        data['LaunchRevenue'] = clean_number(segment_match.group(1)) / 1000
        data['SpaceSystemsRevenue'] = clean_number(segment_match.group(2)) / 1000

    # Look for cash flow data (only in full statements, not all quarterlies)
    cf_match = re.search(
        r'Net cash (?:used in|provided by) operating activities\s*\(?\s*([\d,]+)\)?.*?'
        r'Purchases of property, equipment and software\s*\(\s*([\d,]+)\s*\)',
        content, re.DOTALL
    )
    if cf_match:
        ocf = clean_number(f"({cf_match.group(1)})")  # Usually negative
        capex = clean_number(f"({cf_match.group(2)})")  # Always negative

        data['OperatingCashFlow'] = ocf / 1000 if ocf else None
        data['CapEx'] = capex / 1000 if capex else None
        data['FreeCashFlow'] = ((ocf + capex) / 1000) if (ocf and capex) else None

    # Look for debt information
    debt_match = re.search(
        r'(\d+\.?\d*)\s*million outstanding under.*?Convertible.*?Notes',
        content, re.IGNORECASE
    )
    if debt_match:
        convertible_debt = float(debt_match.group(1))

        # Look for other debt
        other_debt_match = re.search(
            r'(\d+\.?\d*)\s*million outstanding under the Trinity Loan',
            content, re.IGNORECASE
        )
        total_debt = convertible_debt
        if other_debt_match:
            total_debt += float(other_debt_match.group(1))

        data['TotalDebt'] = total_debt

    return data

# Map of files to periods
file_periods = {
    'RKLB_10Q_Q3-2021.txt': 'Q3 2021',
    'RKLB_10Q_Q4-2021.txt': 'Q4 2021',
    'RKLB_10K_FY2021.txt': 'FY2021',
    'RKLB_10Q_Q1-2022.txt': 'Q1 2022',
    'RKLB_10Q_Q2-2022.txt': 'Q2 2022',
    'RKLB_10Q_Q3-2022.txt': 'Q3 2022',
    'RKLB_10K_FY2022.txt': 'FY2022',
    'RKLB_10Q_Q1-2023.txt': 'Q1 2023',
    'RKLB_10Q_Q2-2023.txt': 'Q2 2023',
    'RKLB_10Q_Q3-2023.txt': 'Q3 2023',
    'RKLB_10K_FY2023.txt': 'FY2023',
    'RKLB_10Q_Q1-2024.txt': 'Q1 2024',
    'RKLB_10Q_Q2-2024.txt': 'Q2 2024',
    'RKLB_10Q_Q3-2024.txt': 'Q3 2024',
    'RKLB_10K_FY2024.txt': 'FY2024',
    'RKLB_10Q_Q1-2025.txt': 'Q1 2025',
    'RKLB_10Q_Q2-2025.txt': 'Q2 2025',
    'RKLB_10Q_Q3-2025.txt': 'Q3 2025',
}

# Process all files
extracted_dir = Path('/Users/swilliams/Stocks/Research/RKLB/Extracted')
for filename, period in file_periods.items():
    filepath = extracted_dir / filename
    if filepath.exists():
        data = extract_from_file(filepath, period)
        if data.get('Revenue'):  # Only add if we got revenue data
            metrics.append(data)
    else:
        print(f"Warning: {filename} not found")

# Sort chronologically
period_order = [
    'Q3 2021', 'Q4 2021', 'FY2021',
    'Q1 2022', 'Q2 2022', 'Q3 2022', 'FY2022',
    'Q1 2023', 'Q2 2023', 'Q3 2023', 'FY2023',
    'Q1 2024', 'Q2 2024', 'Q3 2024', 'FY2024',
    'Q1 2025', 'Q2 2025', 'Q3 2025',
]

metrics.sort(key=lambda x: period_order.index(x['Period']) if x['Period'] in period_order else 999)

# Write to CSV
output_file = Path('/Users/swilliams/Stocks/Research/RKLB/Reports/RKLB_Metrics.csv')
output_file.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    'Period', 'Revenue', 'GrossProfit', 'GrossMargin',
    'OperatingIncome', 'OperatingMargin', 'EBITDA',
    'NetIncome', 'NetMargin', 'EPS',
    'OperatingCashFlow', 'CapEx', 'FreeCashFlow',
    'TotalAssets', 'TotalLiabilities', 'ShareholdersEquity',
    'CashAndEquivalents', 'TotalDebt', 'SharesOutstanding',
    'LaunchRevenue', 'SpaceSystemsRevenue', 'Backlog'
]

with open(output_file, 'w', newline='') as csvfile:
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()

    for row in metrics:
        # Round numbers to 1 decimal place
        for key in row:
            if key != 'Period' and isinstance(row[key], (int, float)):
                row[key] = round(row[key], 1)
        writer.writerow(row)

print(f"\nExtracted {len(metrics)} periods")
print(f"CSV written to: {output_file}")
