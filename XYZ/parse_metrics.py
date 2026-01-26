#!/usr/bin/env python3
"""
Parse Block, Inc. (XYZ) financial metrics from SEC filings.
Extracts data from 10-K and 10-Q text files and outputs to CSV.
"""

import re
import csv
import os
from pathlib import Path
from typing import Dict, List, Optional
import glob

# Directory containing extracted text files
EXTRACTED_DIR = Path("/Users/swilliams/Stocks/Research/XYZ/Extracted")
OUTPUT_DIR = Path("/Users/swilliams/Stocks/Research/XYZ/Reports")
OUTPUT_FILE = OUTPUT_DIR / "XYZ_Metrics.csv"

# Ensure output directory exists
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def parse_number(text: str) -> Optional[float]:
    """Parse a number from text, handling parentheses for negatives and commas."""
    if not text or text.strip() in ['', '-', '—', 'N/A']:
        return None

    # Remove $ and whitespace
    text = text.replace('$', '').replace(' ', '').replace(',', '').strip()

    # Handle parentheses as negative
    if '(' in text and ')' in text:
        text = text.replace('(', '-').replace(')', '')

    try:
        return float(text)
    except (ValueError, AttributeError):
        return None


def extract_period_from_filename(filename: str) -> str:
    """Extract period identifier from filename."""
    # XYZ_10K_FY2024.txt -> FY2024
    # XYZ_10Q_Q1-2024.txt -> Q1 2024
    match = re.search(r'_(FY\d{4}|Q\d-\d{4})', filename)
    if match:
        period = match.group(1)
        if period.startswith('Q'):
            # Q1-2024 -> Q1 2024
            return period.replace('-', ' ')
        return period
    return None


def find_financial_table(content: str, pattern: str) -> Optional[str]:
    """Find and extract a section of text containing a financial table."""
    # Look for the pattern and extract surrounding context
    match = re.search(pattern, content, re.IGNORECASE)
    if match:
        # Get context around the match (10000 chars should cover most tables)
        start = max(0, match.start() - 500)
        end = min(len(content), match.end() + 10000)
        return content[start:end]
    return None


def extract_from_table_row(table_section: str, label: str, num_columns: int = 3) -> List[Optional[float]]:
    """Extract numbers from a table row matching the label."""
    # Build pattern to find the row
    # Example: "Total net revenue    1,234    2,345    3,456"
    pattern = rf'{re.escape(label)}\s+([\d,\(\)\s\$]+)'
    match = re.search(pattern, table_section, re.IGNORECASE)

    if not match:
        return [None] * num_columns

    # Extract all numbers from the matched line
    numbers_text = match.group(1)
    # Find all number patterns (including parentheses for negatives)
    number_patterns = re.findall(r'[\(\$]?\s*[\d,]+\s*[\)]?', numbers_text)

    numbers = []
    for num_text in number_patterns[:num_columns]:
        numbers.append(parse_number(num_text))

    # Pad with None if we didn't find enough numbers
    while len(numbers) < num_columns:
        numbers.append(None)

    return numbers[:num_columns]


def parse_10k(filepath: Path) -> Dict[str, any]:
    """Parse annual 10-K filing."""
    print(f"Parsing {filepath.name}...")

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    period = extract_period_from_filename(filepath.name)
    metrics = {'Period': period}

    # Find consolidated statements of operations
    ops_section = find_financial_table(content, r'CONSOLIDATED STATEMENTS OF OPERATIONS')

    if ops_section:
        # Try to extract year from headers (e.g., "2024  2023  2022")
        year_match = re.search(r'(\d{4})\s+(\d{4})\s+(\d{4})', ops_section)

        # Revenue breakdown
        transaction_rev = extract_from_table_row(ops_section, 'Transaction-based', 3)
        subscription_rev = extract_from_table_row(ops_section, 'Subscription and services', 3)
        hardware_rev = extract_from_table_row(ops_section, 'Hardware', 3)
        bitcoin_rev = extract_from_table_row(ops_section, 'Bitcoin', 3)

        metrics['TransactionRevenue'] = transaction_rev[0]
        metrics['SubscriptionRevenue'] = subscription_rev[0]
        metrics['HardwareRevenue'] = hardware_rev[0]
        metrics['BitcoinRevenue'] = bitcoin_rev[0]

        # Total revenue and costs
        total_revenue = extract_from_table_row(ops_section, 'Total net revenue', 3)
        metrics['Revenue'] = total_revenue[0]

        gross_profit = extract_from_table_row(ops_section, 'Gross profit', 3)
        metrics['GrossProfit'] = gross_profit[0]

        if metrics['Revenue'] and metrics['GrossProfit']:
            metrics['GrossMargin'] = round((metrics['GrossProfit'] / metrics['Revenue']) * 100, 1)

        # Operating income/loss
        operating_income = extract_from_table_row(ops_section, 'Operating income', 3)
        if not operating_income[0]:
            operating_income = extract_from_table_row(ops_section, 'Operating loss', 3)
            if operating_income[0]:
                operating_income[0] = -abs(operating_income[0])

        metrics['OperatingIncome'] = operating_income[0]

        if metrics['Revenue'] and metrics['OperatingIncome']:
            metrics['OperatingMargin'] = round((metrics['OperatingIncome'] / metrics['Revenue']) * 100, 1)

        # Net income/loss
        net_income = extract_from_table_row(ops_section, 'Net income', 3)
        if not net_income[0]:
            net_income = extract_from_table_row(ops_section, 'Net loss', 3)
            if net_income[0]:
                net_income[0] = -abs(net_income[0])

        metrics['NetIncome'] = net_income[0]

        if metrics['Revenue'] and metrics['NetIncome']:
            metrics['NetMargin'] = round((metrics['NetIncome'] / metrics['Revenue']) * 100, 1)

        # EPS
        eps_diluted = extract_from_table_row(ops_section, 'Net income.*per share.*[Dd]iluted|Diluted', 3)
        if not eps_diluted[0]:
            eps_diluted = extract_from_table_row(ops_section, 'Net loss.*per share.*[Dd]iluted|Diluted', 3)
        metrics['EPS'] = eps_diluted[0]

    # Find balance sheet
    bs_section = find_financial_table(content, r'CONSOLIDATED BALANCE SHEETS')

    if bs_section:
        # Assets
        cash = extract_from_table_row(bs_section, 'Cash and cash equivalents', 2)
        investments = extract_from_table_row(bs_section, 'Short-term investments', 2)

        if cash[0] and investments[0]:
            metrics['CashAndEquivalents'] = cash[0] + investments[0]
        elif cash[0]:
            metrics['CashAndEquivalents'] = cash[0]

        # Liabilities and Equity
        total_debt_st = extract_from_table_row(bs_section, 'Short-term debt|Current portion.*debt', 2)
        total_debt_lt = extract_from_table_row(bs_section, 'Long-term debt', 2)

        if total_debt_st[0] and total_debt_lt[0]:
            metrics['TotalDebt'] = total_debt_st[0] + total_debt_lt[0]
        elif total_debt_lt[0]:
            metrics['TotalDebt'] = total_debt_lt[0]
        elif total_debt_st[0]:
            metrics['TotalDebt'] = total_debt_st[0]

        equity = extract_from_table_row(bs_section, 'Total stockholders.*equity|Total shareholders.*equity', 2)
        metrics['ShareholdersEquity'] = equity[0]

        shares = extract_from_table_row(bs_section, 'Shares outstanding', 2)
        metrics['SharesOutstanding'] = shares[0]

    # Find cash flow statement
    cf_section = find_financial_table(content, r'CONSOLIDATED STATEMENTS OF CASH FLOWS')

    if cf_section:
        ocf = extract_from_table_row(cf_section, 'Net cash provided by.*operating|Cash flows from operating activities', 3)
        metrics['OperatingCashFlow'] = ocf[0]

        capex = extract_from_table_row(cf_section, 'Purchase.*property and equipment|Capital expenditures', 3)
        if capex[0]:
            metrics['CapitalExpenditures'] = abs(capex[0])  # Make positive

            if metrics.get('OperatingCashFlow'):
                metrics['FreeCashFlow'] = metrics['OperatingCashFlow'] - metrics['CapitalExpenditures']

    # Look for segment/KPI data
    # GPV and Monthly Actives are usually in MD&A or segment notes
    gpv_pattern = r'Gross Payment Volume|GPV.*?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?:billion|million)'
    gpv_match = re.search(gpv_pattern, content, re.IGNORECASE)

    # Square GPV
    square_gpv_pattern = r'Square.*?GPV.*?(\$?\s*\d{1,3}(?:\.\d+)?)\s*billion'
    square_gpv_match = re.search(square_gpv_pattern, content, re.IGNORECASE)

    # Cash App Inflows
    cash_app_pattern = r'Cash App.*?inflow.*?(\$?\s*\d{1,3}(?:\.\d+)?)\s*billion'
    cash_app_match = re.search(cash_app_pattern, content, re.IGNORECASE)

    print(f"  Extracted: Revenue={metrics.get('Revenue')}, Net Income={metrics.get('NetIncome')}")

    return metrics


def parse_10q(filepath: Path) -> Dict[str, any]:
    """Parse quarterly 10-Q filing."""
    print(f"Parsing {filepath.name}...")

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    period = extract_period_from_filename(filepath.name)
    metrics = {'Period': period}

    # Find consolidated statements of operations
    # For quarters, look for "Three months ended" tables
    ops_section = find_financial_table(content, r'CONSOLIDATED STATEMENTS OF OPERATIONS')

    if ops_section:
        # Revenue breakdown (first column is usually current quarter)
        transaction_rev = extract_from_table_row(ops_section, 'Transaction-based', 2)
        subscription_rev = extract_from_table_row(ops_section, 'Subscription and services', 2)
        hardware_rev = extract_from_table_row(ops_section, 'Hardware', 2)
        bitcoin_rev = extract_from_table_row(ops_section, 'Bitcoin', 2)

        metrics['TransactionRevenue'] = transaction_rev[0]
        metrics['SubscriptionRevenue'] = subscription_rev[0]
        metrics['HardwareRevenue'] = hardware_rev[0]
        metrics['BitcoinRevenue'] = bitcoin_rev[0]

        # Total revenue
        total_revenue = extract_from_table_row(ops_section, 'Total net revenue', 2)
        metrics['Revenue'] = total_revenue[0]

        gross_profit = extract_from_table_row(ops_section, 'Gross profit', 2)
        metrics['GrossProfit'] = gross_profit[0]

        if metrics['Revenue'] and metrics['GrossProfit']:
            metrics['GrossMargin'] = round((metrics['GrossProfit'] / metrics['Revenue']) * 100, 1)

        # Operating income/loss
        operating_income = extract_from_table_row(ops_section, 'Operating income', 2)
        if not operating_income[0]:
            operating_income = extract_from_table_row(ops_section, 'Operating loss', 2)
            if operating_income[0]:
                operating_income[0] = -abs(operating_income[0])

        metrics['OperatingIncome'] = operating_income[0]

        if metrics['Revenue'] and metrics['OperatingIncome']:
            metrics['OperatingMargin'] = round((metrics['OperatingIncome'] / metrics['Revenue']) * 100, 1)

        # Net income/loss
        net_income = extract_from_table_row(ops_section, 'Net income', 2)
        if not net_income[0]:
            net_income = extract_from_table_row(ops_section, 'Net loss', 2)
            if net_income[0]:
                net_income[0] = -abs(net_income[0])

        metrics['NetIncome'] = net_income[0]

        if metrics['Revenue'] and metrics['NetIncome']:
            metrics['NetMargin'] = round((metrics['NetIncome'] / metrics['Revenue']) * 100, 1)

        # EPS
        eps_diluted = extract_from_table_row(ops_section, 'Net income.*per share.*[Dd]iluted|Diluted', 2)
        if not eps_diluted[0]:
            eps_diluted = extract_from_table_row(ops_section, 'Net loss.*per share.*[Dd]iluted|Diluted', 2)
        metrics['EPS'] = eps_diluted[0]

    # Balance sheet (use most recent column)
    bs_section = find_financial_table(content, r'CONSOLIDATED BALANCE SHEETS')

    if bs_section:
        cash = extract_from_table_row(bs_section, 'Cash and cash equivalents', 2)
        investments = extract_from_table_row(bs_section, 'Short-term investments', 2)

        if cash[0] and investments[0]:
            metrics['CashAndEquivalents'] = cash[0] + investments[0]
        elif cash[0]:
            metrics['CashAndEquivalents'] = cash[0]

        total_debt_st = extract_from_table_row(bs_section, 'Short-term debt|Current portion.*debt', 2)
        total_debt_lt = extract_from_table_row(bs_section, 'Long-term debt', 2)

        if total_debt_st[0] and total_debt_lt[0]:
            metrics['TotalDebt'] = total_debt_st[0] + total_debt_lt[0]
        elif total_debt_lt[0]:
            metrics['TotalDebt'] = total_debt_lt[0]
        elif total_debt_st[0]:
            metrics['TotalDebt'] = total_debt_st[0]

        equity = extract_from_table_row(bs_section, 'Total stockholders.*equity|Total shareholders.*equity', 2)
        metrics['ShareholdersEquity'] = equity[0]

        shares = extract_from_table_row(bs_section, 'Shares outstanding', 2)
        metrics['SharesOutstanding'] = shares[0]

    # Cash flow
    cf_section = find_financial_table(content, r'CONSOLIDATED STATEMENTS OF CASH FLOWS')

    if cf_section:
        ocf = extract_from_table_row(cf_section, 'Net cash provided by.*operating|Cash flows from operating activities', 2)
        metrics['OperatingCashFlow'] = ocf[0]

        capex = extract_from_table_row(cf_section, 'Purchase.*property and equipment|Capital expenditures', 2)
        if capex[0]:
            metrics['CapitalExpenditures'] = abs(capex[0])

            if metrics.get('OperatingCashFlow'):
                metrics['FreeCashFlow'] = metrics['OperatingCashFlow'] - metrics['CapitalExpenditures']

    print(f"  Extracted: Revenue={metrics.get('Revenue')}, Net Income={metrics.get('NetIncome')}")

    return metrics


def sort_periods(periods_data: List[Dict]) -> List[Dict]:
    """Sort periods chronologically."""
    def period_key(item):
        period = item['Period']
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 4, 0)  # FY goes after Q4
        elif period.startswith('Q'):
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter, 0)
        return (0, 0, 0)

    return sorted(periods_data, key=period_key)


def main():
    """Main parsing function."""
    print("Block, Inc. (XYZ) Financial Metrics Parser")
    print("=" * 60)

    all_metrics = []

    # Parse all 10-K files (annual reports)
    for filepath in sorted(EXTRACTED_DIR.glob("XYZ_10K_*.txt")):
        try:
            metrics = parse_10k(filepath)
            if metrics.get('Period'):
                all_metrics.append(metrics)
        except Exception as e:
            print(f"  ERROR parsing {filepath.name}: {e}")

    # Parse all 10-Q files (quarterly reports)
    for filepath in sorted(EXTRACTED_DIR.glob("XYZ_10Q_*.txt")):
        try:
            metrics = parse_10q(filepath)
            if metrics.get('Period'):
                all_metrics.append(metrics)
        except Exception as e:
            print(f"  ERROR parsing {filepath.name}: {e}")

    # Sort chronologically
    all_metrics = sort_periods(all_metrics)

    # Define CSV columns
    columns = [
        'Period',
        'Revenue',
        'GrossProfit',
        'GrossMargin',
        'OperatingIncome',
        'OperatingMargin',
        'NetIncome',
        'NetMargin',
        'EPS',
        'OperatingCashFlow',
        'CapitalExpenditures',
        'FreeCashFlow',
        'ShareholdersEquity',
        'TotalDebt',
        'CashAndEquivalents',
        'SharesOutstanding',
        'TransactionRevenue',
        'SubscriptionRevenue',
        'HardwareRevenue',
        'BitcoinRevenue',
        'SquareGPV',
        'CashAppInflows',
        'CashAppMonthlyActives',
        'SquareMonthlyActives'
    ]

    # Write to CSV
    print(f"\nWriting {len(all_metrics)} periods to {OUTPUT_FILE}")

    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for metrics in all_metrics:
            # Convert None to empty string for CSV
            row = {col: (metrics.get(col) if metrics.get(col) is not None else '') for col in columns}
            writer.writerow(row)

    print(f"\nDone! Metrics written to: {OUTPUT_FILE}")
    print(f"Total periods: {len(all_metrics)}")

    # Show sample
    if all_metrics:
        print("\nSample (most recent 3 periods):")
        for m in all_metrics[-3:]:
            print(f"  {m['Period']}: Revenue=${m.get('Revenue', 'N/A')}M, Net Income=${m.get('NetIncome', 'N/A')}M")


if __name__ == '__main__':
    main()
