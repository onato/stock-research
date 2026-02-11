#!/usr/bin/env python3
"""
Extract Visa financial metrics from iXBRL text files.
These files have line numbers prepended, so we need to parse accordingly.
"""

import re
import csv
from pathlib import Path
from typing import Dict, Optional, List

def read_file_lines(file_path: str) -> List[str]:
    """Read file and return list of lines (text after line number)."""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = []
        for line in f:
            # Skip the line number prefix (e.g., "4110→")
            if '→' in line:
                parts = line.split('→', 1)
                if len(parts) == 2:
                    lines.append(parts[1].strip())
                else:
                    lines.append(line.strip())
            else:
                lines.append(line.strip())
        return lines

def find_section(lines: List[str], section_name: str) -> int:
    """Find the line index where a section starts."""
    for i, line in enumerate(lines):
        if section_name.lower() in line.lower():
            return i
    return -1

def extract_table_values(lines: List[str], start_idx: int, keyword: str, num_columns: int = 3) -> List[Optional[float]]:
    """
    Extract numeric values from a financial statement table.
    Returns list of values [current_year, prior_year, two_years_prior]
    """
    # Find the keyword line
    found_idx = -1
    for i in range(start_idx, min(start_idx + 200, len(lines))):
        if keyword.lower() in lines[i].lower():
            found_idx = i
            break

    if found_idx == -1:
        return [None] * num_columns

    # Look for numeric values in the next 10 lines
    values = []
    for i in range(found_idx, min(found_idx + 15, len(lines))):
        line = lines[i]
        # Remove common separators
        line = line.replace('$', '').replace(',', '').replace('(', '-').replace(')', '')

        # Look for standalone numbers
        if line.strip() and line.strip().replace('.', '').replace('-', '').isdigit():
            try:
                val = float(line.strip())
                # Filter out line numbers and years
                if val > 100 or (val > 0 and val < 100 and '.' in line):  # Allow decimals
                    values.append(val)
            except ValueError:
                continue

        if len(values) >= num_columns:
            break

    # Pad with None if not enough values found
    while len(values) < num_columns:
        values.append(None)

    return values[:num_columns]

def extract_10k_metrics(file_path: str, fiscal_year: int) -> Dict:
    """Extract metrics from a 10-K filing."""
    lines = read_file_lines(file_path)

    metrics = {
        'Period': f'FY{fiscal_year}',
        'Revenue': None,
        'OperatingIncome': None,
        'NetIncome': None,
        'EPS': None,
        'OperatingMargin': None,
        'NetMargin': None,
        'PaymentsVolume': None,
        'ProcessedTransactions': None,
        'CrossBorderVolumeGrowth': None,
        'ClientIncentives': None,
        'ShareholdersEquity': None,
        'TotalDebt': None,
        'CashAndEquivalents': None,
        'SharesOutstanding': None,
        'FreeCashFlow': None,
    }

    # Find income statement
    income_idx = find_section(lines, 'CONSOLIDATED STATEMENTS OF OPERATIONS')
    if income_idx != -1:
        # Extract values (first column is current year)
        rev_vals = extract_table_values(lines, income_idx, 'Net revenue')
        if rev_vals[0]:
            metrics['Revenue'] = rev_vals[0]

        op_inc_vals = extract_table_values(lines, income_idx, 'Operating income')
        if op_inc_vals[0]:
            metrics['OperatingIncome'] = op_inc_vals[0]

        net_inc_vals = extract_table_values(lines, income_idx, 'Net income')
        if net_inc_vals[0]:
            metrics['NetIncome'] = net_inc_vals[0]

        # EPS - look for "Diluted Earnings Per Share" then "Class A common stock"
        diluted_idx = find_section(lines[income_idx:income_idx+300], 'Diluted Earnings Per Share')
        if diluted_idx != -1:
            eps_vals = extract_table_values(lines, income_idx + diluted_idx + 1, 'Class A common stock', num_columns=3)
            if eps_vals[0]:
                metrics['EPS'] = eps_vals[0]

    # Calculate margins
    if metrics['Revenue'] and metrics['OperatingIncome']:
        metrics['OperatingMargin'] = round(metrics['OperatingIncome'] / metrics['Revenue'] * 100, 1)
    if metrics['Revenue'] and metrics['NetIncome']:
        metrics['NetMargin'] = round(metrics['NetIncome'] / metrics['Revenue'] * 100, 1)

    # Find balance sheet
    balance_idx = find_section(lines, 'CONSOLIDATED BALANCE SHEETS')
    if balance_idx != -1:
        cash_vals = extract_table_values(lines, balance_idx, 'Cash and cash equivalents', num_columns=2)
        if cash_vals[0]:
            metrics['CashAndEquivalents'] = cash_vals[0]

        equity_vals = extract_table_values(lines, balance_idx, 'Total equity', num_columns=2)
        if equity_vals[0]:
            metrics['ShareholdersEquity'] = equity_vals[0]

        debt_vals = extract_table_values(lines, balance_idx, 'Long-term debt', num_columns=2)
        if debt_vals[0]:
            metrics['TotalDebt'] = debt_vals[0]

    # Find cash flow statement
    cashflow_idx = find_section(lines, 'CONSOLIDATED STATEMENTS OF CASH FLOWS')
    if cashflow_idx != -1:
        opcf_vals = extract_table_values(lines, cashflow_idx, 'Net cash provided by (used in) operating activities')
        capex_vals = extract_table_values(lines, cashflow_idx, 'Purchases of property, equipment and technology')

        if opcf_vals[0] and capex_vals[0]:
            metrics['FreeCashFlow'] = round(opcf_vals[0] - abs(capex_vals[0]), 1)

    # Business metrics - look for payments volume table
    text = '\n'.join(lines)

    # Total nominal payments volume (in billions)
    pv_match = re.search(r'Total nominal payments volume.*?\$?\s*(\d{1,3}(?:,\d{3})*)', text, re.DOTALL | re.IGNORECASE)
    if pv_match:
        pv_str = pv_match.group(1).replace(',', '')
        metrics['PaymentsVolume'] = float(pv_str)

    # Processed transactions (in millions, convert to billions)
    pt_match = re.search(r'Visa processed transactions\s+(\d{1,3}(?:,\d{3})*)', text)
    if pt_match:
        pt_str = pt_match.group(1).replace(',', '')
        metrics['ProcessedTransactions'] = round(float(pt_str) / 1000, 1)

    # Client incentives - from cash flow statement
    ci_match = re.search(r'Client incentives\s+(\d{1,3}(?:,\d{3})*)', text)
    if ci_match:
        ci_str = ci_match.group(1).replace(',', '')
        metrics['ClientIncentives'] = float(ci_str)

    # Shares outstanding from weighted average diluted
    shares_idx = find_section(lines, 'Diluted Weighted-average Shares Outstanding')
    if shares_idx == -1:
        shares_idx = find_section(lines, 'Basic Weighted-average Shares Outstanding')

    if shares_idx != -1:
        # Look for Class A shares
        shares_vals = extract_table_values(lines, shares_idx, 'Class A common stock', num_columns=3)
        if shares_vals[0]:
            metrics['SharesOutstanding'] = shares_vals[0]

    return metrics

def extract_10q_metrics(file_path: str, quarter: int, fiscal_year: int) -> Dict:
    """Extract metrics from a 10-Q filing."""
    lines = read_file_lines(file_path)

    metrics = {
        'Period': f'Q{quarter} FY{fiscal_year}',
        'Revenue': None,
        'OperatingIncome': None,
        'NetIncome': None,
        'EPS': None,
        'OperatingMargin': None,
        'NetMargin': None,
        'PaymentsVolume': None,
        'ProcessedTransactions': None,
        'CrossBorderVolumeGrowth': None,
        'ClientIncentives': None,
        'ShareholdersEquity': None,
        'TotalDebt': None,
        'CashAndEquivalents': None,
        'SharesOutstanding': None,
        'FreeCashFlow': None,
    }

    # Find "Three Months Ended" section
    three_mo_idx = find_section(lines, 'Three Months Ended')
    if three_mo_idx != -1:
        # Look for income statement data
        rev_vals = extract_table_values(lines, three_mo_idx, 'Net revenue', num_columns=2)
        if rev_vals[0]:
            metrics['Revenue'] = rev_vals[0]

        op_inc_vals = extract_table_values(lines, three_mo_idx, 'Operating income', num_columns=2)
        if op_inc_vals[0]:
            metrics['OperatingIncome'] = op_inc_vals[0]

        net_inc_vals = extract_table_values(lines, three_mo_idx, 'Net income', num_columns=2)
        if net_inc_vals[0]:
            metrics['NetIncome'] = net_inc_vals[0]

        # EPS
        diluted_idx = find_section(lines[three_mo_idx:three_mo_idx+200], 'Diluted Earnings Per Share')
        if diluted_idx != -1:
            eps_vals = extract_table_values(lines, three_mo_idx + diluted_idx + 1, 'Class A common stock', num_columns=2)
            if eps_vals[0]:
                metrics['EPS'] = eps_vals[0]

    # Calculate margins
    if metrics['Revenue'] and metrics['OperatingIncome']:
        metrics['OperatingMargin'] = round(metrics['OperatingIncome'] / metrics['Revenue'] * 100, 1)
    if metrics['Revenue'] and metrics['NetIncome']:
        metrics['NetMargin'] = round(metrics['NetIncome'] / metrics['Revenue'] * 100, 1)

    return metrics

def main():
    base_dir = Path('/Users/swilliams/Stocks/Research/V')
    extracted_dir = base_dir / 'Extracted'
    output_dir = base_dir / 'Reports'
    output_dir.mkdir(exist_ok=True)

    all_metrics = []

    # Process all files
    files = sorted(extracted_dir.glob('V_*.txt'))

    for file_path in files:
        filename = file_path.name
        print(f'Processing {filename}...')

        try:
            if filename.startswith('V_10K_FY'):
                # Extract fiscal year
                fy_match = re.search(r'FY(\d{4})', filename)
                if fy_match:
                    fiscal_year = int(fy_match.group(1))
                    metrics = extract_10k_metrics(str(file_path), fiscal_year)
                    all_metrics.append(metrics)
                    print(f"  {metrics['Period']}: Rev=${metrics.get('Revenue', 'N/A')}M, "
                          f"NI=${metrics.get('NetIncome', 'N/A')}M, EPS=${metrics.get('EPS', 'N/A')}")

            elif filename.startswith('V_10Q_Q'):
                # Extract quarter and year
                q_match = re.search(r'Q(\d)-(\d{4})', filename)
                if q_match:
                    quarter = int(q_match.group(1))
                    calendar_year = int(q_match.group(2))
                    fiscal_year = calendar_year  # Q1-2024 is Q1 FY2024

                    metrics = extract_10q_metrics(str(file_path), quarter, fiscal_year)
                    all_metrics.append(metrics)
                    print(f"  {metrics['Period']}: Rev=${metrics.get('Revenue', 'N/A')}M, "
                          f"NI=${metrics.get('NetIncome', 'N/A')}M")

        except Exception as e:
            print(f"  ERROR: {e}")
            continue

    # Sort chronologically
    def sort_key(m):
        period = m['Period']
        if period.startswith('FY'):
            fy = int(period[2:])
            return (fy, 4)  # FY reports sort after Q3
        else:
            q_match = re.match(r'Q(\d) FY(\d{4})', period)
            if q_match:
                q = int(q_match.group(1))
                fy = int(q_match.group(2))
                return (fy, q)
        return (0, 0)

    all_metrics.sort(key=sort_key)

    # Write to CSV
    output_file = output_dir / 'V_Metrics.csv'
    fieldnames = ['Period', 'Revenue', 'OperatingIncome', 'NetIncome', 'EPS', 'OperatingMargin',
                  'NetMargin', 'PaymentsVolume', 'ProcessedTransactions', 'CrossBorderVolumeGrowth',
                  'ClientIncentives', 'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents',
                  'SharesOutstanding', 'FreeCashFlow']

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for metrics in all_metrics:
            # Convert None to empty string
            row = {k: ('' if v is None else v) for k, v in metrics.items()}
            writer.writerow(row)

    print(f'\n✓ Wrote {len(all_metrics)} periods to {output_file}')
    print(f'\nSummary:')
    print(f'  Earliest: {all_metrics[0]["Period"]}')
    print(f'  Latest: {all_metrics[-1]["Period"]}')

if __name__ == '__main__':
    main()
