#!/usr/bin/env python3
"""
Parse Visa SEC filings to extract financial metrics.
Reads from V/Extracted/*.txt and outputs to V/Reports/V_Metrics.csv
"""

import re
import os
from pathlib import Path
from typing import Dict, Optional
import csv

def extract_number(text: str, start_pos: int, search_back: int = 200) -> Optional[float]:
    """Extract a number near a position in text, looking backwards."""
    snippet = text[max(0, start_pos - search_back):start_pos + 100]
    # Look for patterns like "$\n19,743" or "19,743" or "19.73"
    patterns = [
        r'\$\s*\n?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)',  # $19,743 or $ 19,743
        r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?)',  # 19,743 or 19.73
    ]

    for pattern in patterns:
        matches = list(re.finditer(pattern, snippet))
        if matches:
            # Get the last match (closest to our search term)
            last_match = matches[-1]
            num_str = last_match.group(1).replace(',', '')
            try:
                return float(num_str)
            except ValueError:
                continue
    return None

def find_value_in_section(text: str, section_start: int, section_end: int,
                         keywords: list, year_col: int = 0) -> Optional[float]:
    """
    Find a value in a financial statement section.
    year_col: 0 = current year (leftmost), 1 = prior year, 2 = two years prior
    """
    section = text[section_start:section_end]

    for keyword in keywords:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        match = pattern.search(section)
        if match:
            # Find all numbers after this match
            after_text = section[match.end():match.end() + 300]
            numbers = re.findall(r'\$?\s*\n?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)', after_text)

            # Skip numbers that look like line numbers or years
            valid_numbers = []
            for num_str in numbers:
                num_str_clean = num_str.replace(',', '')
                num = float(num_str_clean)
                if num > 2000 and num < 2100:  # Skip years
                    continue
                if num < 10:  # Skip percentages without % sign
                    continue
                valid_numbers.append(num)

            if len(valid_numbers) > year_col:
                return valid_numbers[year_col]

    return None

def parse_10k(file_path: str, fiscal_year: int) -> Dict:
    """Parse a 10-K filing for annual metrics."""
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read()

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

    # Find consolidated statements section
    income_stmt_match = re.search(r'CONSOLIDATED STATEMENTS OF OPERATIONS', text, re.IGNORECASE)
    balance_sheet_match = re.search(r'CONSOLIDATED BALANCE SHEETS', text, re.IGNORECASE)
    cash_flow_match = re.search(r'CONSOLIDATED STATEMENTS OF CASH FLOWS', text, re.IGNORECASE)

    if income_stmt_match:
        # Income statement typically has 3 years of data
        stmt_start = income_stmt_match.end()
        stmt_end = min(stmt_start + 5000, len(text))

        metrics['Revenue'] = find_value_in_section(text, stmt_start, stmt_end,
                                                   ['Net revenue', 'Net revenues'], year_col=0)
        metrics['OperatingIncome'] = find_value_in_section(text, stmt_start, stmt_end,
                                                           ['Operating income'], year_col=0)
        metrics['NetIncome'] = find_value_in_section(text, stmt_start, stmt_end,
                                                     ['Net income'], year_col=0)
        metrics['EPS'] = find_value_in_section(text, stmt_start, stmt_end,
                                               ['Diluted Earnings Per Share', 'Class A common stock'],
                                               year_col=0)

        # Calculate margins
        if metrics['Revenue'] and metrics['OperatingIncome']:
            metrics['OperatingMargin'] = round(metrics['OperatingIncome'] / metrics['Revenue'] * 100, 1)
        if metrics['Revenue'] and metrics['NetIncome']:
            metrics['NetMargin'] = round(metrics['NetIncome'] / metrics['Revenue'] * 100, 1)

    if balance_sheet_match:
        bs_start = balance_sheet_match.end()
        bs_end = min(bs_start + 5000, len(text))

        metrics['CashAndEquivalents'] = find_value_in_section(text, bs_start, bs_end,
                                                              ['Cash and cash equivalents'], year_col=0)
        metrics['ShareholdersEquity'] = find_value_in_section(text, bs_start, bs_end,
                                                              ['Total equity', 'Total stockholders'], year_col=0)
        metrics['TotalDebt'] = find_value_in_section(text, bs_start, bs_end,
                                                     ['Long-term debt'], year_col=0)

    if cash_flow_match:
        cf_start = cash_flow_match.end()
        cf_end = min(cf_start + 5000, len(text))

        operating_cf = find_value_in_section(text, cf_start, cf_end,
                                            ['Net cash provided by (used in) operating activities'], year_col=0)
        capex = find_value_in_section(text, cf_start, cf_end,
                                     ['Purchases of property, equipment and technology'], year_col=0)

        if operating_cf and capex:
            metrics['FreeCashFlow'] = round(operating_cf - abs(capex), 1)

    # Look for business metrics - payments volume
    payments_vol_match = re.search(r'Total nominal payments volume.*?\$\s*(\d{1,3}(?:,\d{3})*)',
                                   text, re.IGNORECASE | re.DOTALL)
    if payments_vol_match:
        vol_str = payments_vol_match.group(1).replace(',', '')
        metrics['PaymentsVolume'] = float(vol_str)

    # Processed transactions (in millions)
    proc_trans_match = re.search(r'Visa processed transactions\s+(\d{1,3}(?:,\d{3})*)',
                                 text, re.IGNORECASE)
    if proc_trans_match:
        trans_str = proc_trans_match.group(1).replace(',', '')
        metrics['ProcessedTransactions'] = round(float(trans_str) / 1000, 1)  # Convert millions to billions

    # Client incentives
    client_inc_match = re.search(r'Client incentives\s+(\d{1,3}(?:,\d{3})*)', text)
    if client_inc_match:
        inc_str = client_inc_match.group(1).replace(',', '')
        metrics['ClientIncentives'] = float(inc_str)

    # Shares outstanding (diluted weighted average)
    shares_match = re.search(r'Class A common stock\s+(\d{1,3}(?:,\d{3})*)\s+\d{1,3}(?:,\d{3})*\s+\d{1,3}(?:,\d{3})*\s+Diluted',
                            text, re.DOTALL)
    if shares_match:
        shares_str = shares_match.group(1).replace(',', '')
        metrics['SharesOutstanding'] = float(shares_str)

    return metrics

def parse_10q(file_path: str, quarter: int, fiscal_year: int) -> Dict:
    """Parse a 10-Q filing for quarterly metrics."""
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read()

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

    # Look for "Three months ended" section which has quarterly (not YTD) numbers
    three_months_match = re.search(r'Three [Mm]onths [Ee]nded', text)

    if three_months_match:
        stmt_start = three_months_match.start()
        stmt_end = min(stmt_start + 5000, len(text))

        # Look for consolidated statements of operations near this section
        section = text[max(0, stmt_start - 1000):stmt_end]

        metrics['Revenue'] = find_value_in_section(text, stmt_start, stmt_end,
                                                   ['Net revenue', 'Net revenues'], year_col=0)
        metrics['OperatingIncome'] = find_value_in_section(text, stmt_start, stmt_end,
                                                           ['Operating income'], year_col=0)
        metrics['NetIncome'] = find_value_in_section(text, stmt_start, stmt_end,
                                                     ['Net income'], year_col=0)
        metrics['EPS'] = find_value_in_section(text, stmt_start, stmt_end,
                                               ['Diluted earnings per share', 'Class A common stock'],
                                               year_col=0)

        # Calculate margins
        if metrics['Revenue'] and metrics['OperatingIncome']:
            metrics['OperatingMargin'] = round(metrics['OperatingIncome'] / metrics['Revenue'] * 100, 1)
        if metrics['Revenue'] and metrics['NetIncome']:
            metrics['NetMargin'] = round(metrics['NetIncome'] / metrics['Revenue'] * 100, 1)

    return metrics

def main():
    extracted_dir = Path('/Users/swilliams/Stocks/Research/V/Extracted')
    output_dir = Path('/Users/swilliams/Stocks/Research/V/Reports')
    output_dir.mkdir(exist_ok=True)

    all_metrics = []

    # Parse all files
    files = sorted(extracted_dir.glob('*.txt'))

    for file_path in files:
        filename = file_path.name
        print(f'Processing {filename}...')

        if filename.startswith('V_10K_FY'):
            # Annual report
            fy_match = re.search(r'FY(\d{4})', filename)
            if fy_match:
                fiscal_year = int(fy_match.group(1))
                metrics = parse_10k(str(file_path), fiscal_year)
                all_metrics.append(metrics)
                print(f"  FY{fiscal_year}: Revenue=${metrics.get('Revenue', 'N/A')}M, NetIncome=${metrics.get('NetIncome', 'N/A')}M")

        elif filename.startswith('V_10Q_Q'):
            # Quarterly report
            q_match = re.search(r'Q(\d)-(\d{4})', filename)
            if q_match:
                quarter = int(q_match.group(1))
                calendar_year = int(q_match.group(2))

                # Convert calendar year to fiscal year
                # Q1 FY2024 = Q1-2024 (Oct-Dec 2023)
                # Q2 FY2024 = Q2-2024 (Jan-Mar 2024)
                # Q3 FY2024 = Q3-2024 (Apr-Jun 2024)
                fiscal_year = calendar_year

                metrics = parse_10q(str(file_path), quarter, fiscal_year)
                all_metrics.append(metrics)
                print(f"  Q{quarter} FY{fiscal_year}: Revenue=${metrics.get('Revenue', 'N/A')}M")

    # Sort chronologically
    def sort_key(m):
        period = m['Period']
        if period.startswith('FY'):
            fy = int(period[2:])
            return (fy, 4)  # Annual reports sort after Q3
        else:
            q_match = re.match(r'Q(\d) FY(\d{4})', period)
            if q_match:
                q = int(q_match.group(1))
                fy = int(q_match.group(2))
                return (fy, q)
        return (0, 0)

    all_metrics.sort(key=sort_key)

    # Write CSV
    output_file = output_dir / 'V_Metrics.csv'
    fieldnames = ['Period', 'Revenue', 'OperatingIncome', 'NetIncome', 'EPS', 'OperatingMargin',
                  'NetMargin', 'PaymentsVolume', 'ProcessedTransactions', 'CrossBorderVolumeGrowth',
                  'ClientIncentives', 'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents',
                  'SharesOutstanding', 'FreeCashFlow']

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for metrics in all_metrics:
            # Convert None to empty string for CSV
            row = {k: ('' if v is None else v) for k, v in metrics.items()}
            writer.writerow(row)

    print(f'\nWrote {len(all_metrics)} periods to {output_file}')

if __name__ == '__main__':
    main()
