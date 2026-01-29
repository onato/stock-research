#!/usr/bin/env python3
"""
Parse NetEase (9999.HK) financial metrics from extracted text files
and write to CSV format.
"""

import os
import re
import csv
from pathlib import Path
from collections import defaultdict

EXTRACTED_DIR = Path("/Users/swilliams/Stocks/Research/9999.HK/Extracted")
OUTPUT_CSV = Path("/Users/swilliams/Stocks/Research/9999.HK/Reports/9999.HK_Metrics.csv")

# Ensure output directory exists
OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

def parse_rmb_value(text):
    """Extract RMB value from text like 'RMB28.4 billion' or 'RMB1,234.5 million'"""
    # Remove commas
    text = text.replace(',', '')

    # Match patterns like "RMB28.4 billion" or "RMB1234.5 million"
    match = re.search(r'RMB\s*([\d.]+)\s*(billion|million)', text, re.IGNORECASE)
    if match:
        value = float(match.group(1))
        unit = match.group(2).lower()
        if unit == 'billion':
            return value * 1000  # Convert to millions
        else:
            return value

    # Match patterns like "RMB 1,234.5" or "RMB1234"
    match = re.search(r'RMB\s*([\d.]+)', text)
    if match:
        return float(match.group(1))

    return None

def extract_table_value(lines, row_pattern, col_index=1):
    """Extract value from a table row matching pattern"""
    for i, line in enumerate(lines):
        if re.search(row_pattern, line, re.IGNORECASE):
            # Look for numbers in this line or next few lines
            for j in range(i, min(i + 3, len(lines))):
                # Find all numbers in the line
                numbers = re.findall(r'([\d,]+\.?\d*)', lines[j])
                if numbers and len(numbers) >= col_index:
                    val_str = numbers[col_index - 1].replace(',', '')
                    try:
                        return float(val_str)
                    except:
                        pass
    return None

def parse_file(filepath):
    """Parse a single earnings file and extract metrics"""
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        lines = content.split('\n')

    filename = filepath.name
    # Extract period from filename: 9999.HK_6K_Q1-2025.txt or 9999.HK_6K_Q4-2024.txt
    period_match = re.search(r'Q(\d)[-_](\d{4})', filename)
    if not period_match:
        return []

    quarter = period_match.group(1)
    year = period_match.group(2)
    period = f"Q{quarter} {year}"

    metrics = {}

    # Extract Revenue
    revenue_patterns = [
        r'Net revenues?\s+(?:were|for.*?(?:were|was))\s+RMB\s*([\d.]+)\s*(billion|million)',
        r'Total net revenues.*?RMB\s*([\d.]+)\s*(billion|million)',
    ]
    for pattern in revenue_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            metrics['Revenue'] = value * 1000 if unit == 'billion' else value
            break

    # Extract Gross Profit
    gp_patterns = [
        r'Gross profit\s+(?:was|for.*?(?:were|was))\s+RMB\s*([\d.]+)\s*(billion|million)',
    ]
    for pattern in gp_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            metrics['GrossProfit'] = value * 1000 if unit == 'billion' else value
            break

    # Calculate Gross Margin
    if 'Revenue' in metrics and 'GrossProfit' in metrics and metrics['Revenue'] > 0:
        metrics['GrossMargin'] = round((metrics['GrossProfit'] / metrics['Revenue']) * 100, 1)

    # Extract Net Income
    ni_patterns = [
        r'Net income attributable to the Company\'s shareholders\s+(?:was|totaled)\s+RMB\s*([\d.]+)\s*(billion|million)',
        r'Net income attributable to.*?shareholders.*?RMB\s*([\d.]+)\s*(billion|million)',
    ]
    for pattern in ni_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            metrics['NetIncome'] = value * 1000 if unit == 'billion' else value
            break

    # Calculate Net Margin
    if 'Revenue' in metrics and 'NetIncome' in metrics and metrics['Revenue'] > 0:
        metrics['NetMargin'] = round((metrics['NetIncome'] / metrics['Revenue']) * 100, 1)

    # Extract EPS (per ADS)
    eps_patterns = [
        r'Basic net income(?:\s+per\s+share)?\s+was\s+US\$[\d.]+\s+(?:per\s+share\s+)?\(US\$([\d.]+)\s+per\s+ADS\)',
        r'Earnings per ADS,?\s+basic.*?US\$([\d.]+)',
        r'basic.*?US\$([\d.]+)\s+per\s+ADS',
    ]
    for pattern in eps_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            metrics['EPS'] = float(match.group(1))
            break

    # Extract Operating Income from tables
    # Look for "Operating profit" or "Operating income" in financial statements
    op_income_patterns = [
        r'Operating\s+(?:profit|income).*?RMB\s*([\d.]+)\s*(billion|million)',
    ]
    for pattern in op_income_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            metrics['OperatingIncome'] = value * 1000 if unit == 'billion' else value
            break

    # Calculate Operating Margin
    if 'Revenue' in metrics and 'OperatingIncome' in metrics and metrics['Revenue'] > 0:
        metrics['OperatingMargin'] = round((metrics['OperatingIncome'] / metrics['Revenue']) * 100, 1)

    # Extract segment revenues (recent format)
    # Games and related value-added services
    game_patterns = [
        r'Games and related value-added services net revenues?\s+(?:were|was)\s+RMB\s*([\d.]+)\s*(billion|million)',
    ]
    for pattern in game_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            metrics['GameRevenue'] = value * 1000 if unit == 'billion' else value
            break

    # Youdao
    youdao_patterns = [
        r'Youdao net revenues?\s+(?:were|was)\s+RMB\s*([\d.]+)\s*(billion|million)',
    ]
    for pattern in youdao_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            metrics['YoudaoRevenue'] = value * 1000 if unit == 'billion' else value
            break

    # Cloud Music
    cloud_patterns = [
        r'(?:NetEase )?Cloud Music net revenues?\s+(?:were|was)\s+RMB\s*([\d.]+)\s*(billion|million)',
    ]
    for pattern in cloud_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            metrics['CloudMusicRevenue'] = value * 1000 if unit == 'billion' else value
            break

    # Innovative businesses
    innov_patterns = [
        r'Innovative businesses and others net revenues?\s+(?:were|was)\s+RMB\s*([\d.]+)\s*(billion|million)',
    ]
    for pattern in innov_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            metrics['InnovativeRevenue'] = value * 1000 if unit == 'billion' else value
            break

    # For older format, look for "Online game services" revenue
    if 'GameRevenue' not in metrics:
        old_game_patterns = [
            r'(?:Revenues from )?[Oo]nline game services?\s+(?:were|was)\s+RMB\s*([\d,.]+)\s*(billion|million)',
        ]
        for pattern in old_game_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                value_str = match.group(1).replace(',', '')
                value = float(value_str)
                unit = match.group(2).lower()
                metrics['GameRevenue'] = value * 1000 if unit == 'billion' else value
                break

    # Extract balance sheet items from tables
    # Look for balance sheet section
    balance_sheet_start = content.find('CONSOLIDATED BALANCE SHEET')
    if balance_sheet_start > 0:
        balance_content = content[balance_sheet_start:balance_sheet_start + 20000]

        # Cash and equivalents
        cash_match = re.search(r'Cash and cash equivalents\s+[\d,]+\s+([\d,]+)', balance_content)
        if cash_match:
            metrics['CashAndEquivalents'] = float(cash_match.group(1).replace(',', ''))

        # Total debt (short-term + long-term loans)
        short_debt_match = re.search(r'Short-term loans?\s+[\d,]+\s+([\d,]+)', balance_content)
        long_debt_match = re.search(r'Long-term loans?\s+[\d,]+\s+([\d,]+)', balance_content)

        total_debt = 0
        if short_debt_match:
            total_debt += float(short_debt_match.group(1).replace(',', ''))
        if long_debt_match:
            try:
                total_debt += float(long_debt_match.group(1).replace(',', ''))
            except:
                pass
        if total_debt > 0:
            metrics['TotalDebt'] = total_debt

        # Shareholders' equity
        equity_match = re.search(r'NetEase.*?shareholders?\' equity\s+[\d,]+\s+([\d,]+)', balance_content)
        if equity_match:
            metrics['ShareholdersEquity'] = float(equity_match.group(1).replace(',', ''))

    # Extract cash flow from operations
    cash_flow_start = content.find('STATEMENTS OF CASH FLOW')
    if cash_flow_start > 0:
        cf_content = content[cash_flow_start:cash_flow_start + 15000]

        # Operating cash flow
        ocf_match = re.search(r'Net cash provided by operating activities\s+[\d,]+\s+[\d,]+\s+([\d,]+)', cf_content)
        if not ocf_match:
            ocf_match = re.search(r'Net cash provided by operating activities\s+[\d,]+\s+([\d,]+)', cf_content)
        if ocf_match:
            metrics['OperatingCashFlow'] = float(ocf_match.group(1).replace(',', ''))

    # Also look for operating cash flow in highlights
    ocf_highlight = re.search(r'Net cash provided by operating activities was RMB\s*([\d.]+)\s*(billion|million)', content)
    if ocf_highlight and 'OperatingCashFlow' not in metrics:
        value = float(ocf_highlight.group(1))
        unit = ocf_highlight.group(2).lower()
        metrics['OperatingCashFlow'] = value * 1000 if unit == 'billion' else value

    results = [{'Period': period, **metrics}]

    # For Q4 files, also extract full year data
    if quarter == '4':
        fy_metrics = {}

        # Look for fiscal year section
        fy_section_match = re.search(r'(?:Fiscal Year|Year Ended|Full Year) (\d{4})', content, re.IGNORECASE)
        if fy_section_match:
            fy_year = fy_section_match.group(1)
            fy_period = f"FY{fy_year}"

            # Extract FY revenue
            fy_rev_patterns = [
                rf'(?:Net revenues|Total net revenues).*?(?:fiscal year|year ended).*?{fy_year}.*?RMB\s*([\d.]+)\s*(billion|million)',
                rf'(?:fiscal year|year) {fy_year}.*?(?:Net revenues|Total net revenues).*?RMB\s*([\d.]+)\s*(billion|million)',
            ]
            for pattern in fy_rev_patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).lower()
                    fy_metrics['Revenue'] = value * 1000 if unit == 'billion' else value
                    break

            # Extract FY gross profit
            fy_gp_patterns = [
                rf'Gross profit.*?(?:fiscal year|year ended).*?{fy_year}.*?RMB\s*([\d.]+)\s*(billion|million)',
            ]
            for pattern in fy_gp_patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).lower()
                    fy_metrics['GrossProfit'] = value * 1000 if unit == 'billion' else value
                    break

            # Calculate FY Gross Margin
            if 'Revenue' in fy_metrics and 'GrossProfit' in fy_metrics and fy_metrics['Revenue'] > 0:
                fy_metrics['GrossMargin'] = round((fy_metrics['GrossProfit'] / fy_metrics['Revenue']) * 100, 1)

            # Extract FY net income
            fy_ni_patterns = [
                rf'Net income attributable to.*?shareholders.*?(?:fiscal year|year ended|year)\s+{fy_year}.*?RMB\s*([\d.]+)\s*(billion|million)',
            ]
            for pattern in fy_ni_patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).lower()
                    fy_metrics['NetIncome'] = value * 1000 if unit == 'billion' else value
                    break

            # Calculate FY Net Margin
            if 'Revenue' in fy_metrics and 'NetIncome' in fy_metrics and fy_metrics['Revenue'] > 0:
                fy_metrics['NetMargin'] = round((fy_metrics['NetIncome'] / fy_metrics['Revenue']) * 100, 1)

            # Extract FY EPS
            fy_eps_patterns = [
                rf'(?:fiscal year|year) {fy_year}.*?Basic net income.*?US\$([\d.]+)\s+per\s+ADS',
                rf'fiscal year {fy_year}.*?Earnings per ADS,?\s+basic.*?US\$([\d.]+)',
            ]
            for pattern in fy_eps_patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    fy_metrics['EPS'] = float(match.group(1))
                    break

            # Extract FY segment revenues
            # Games
            fy_game_patterns = [
                rf'(?:fiscal year|year) {fy_year}.*?Games and related value-added services.*?RMB\s*([\d.]+)\s*(billion|million)',
            ]
            for pattern in fy_game_patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).lower()
                    fy_metrics['GameRevenue'] = value * 1000 if unit == 'billion' else value
                    break

            # Youdao
            fy_youdao_patterns = [
                rf'(?:fiscal year|year) {fy_year}.*?Youdao.*?RMB\s*([\d.]+)\s*(billion|million)',
            ]
            for pattern in fy_youdao_patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).lower()
                    fy_metrics['YoudaoRevenue'] = value * 1000 if unit == 'billion' else value
                    break

            # Cloud Music
            fy_cloud_patterns = [
                rf'(?:fiscal year|year) {fy_year}.*?Cloud Music.*?RMB\s*([\d.]+)\s*(billion|million)',
            ]
            for pattern in fy_cloud_patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).lower()
                    fy_metrics['CloudMusicRevenue'] = value * 1000 if unit == 'billion' else value
                    break

            # Innovative
            fy_innov_patterns = [
                rf'(?:fiscal year|year) {fy_year}.*?Innovative businesses and others.*?RMB\s*([\d.]+)\s*(billion|million)',
            ]
            for pattern in fy_innov_patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).lower()
                    fy_metrics['InnovativeRevenue'] = value * 1000 if unit == 'billion' else value
                    break

            # Extract FY operating cash flow
            fy_ocf_patterns = [
                rf'Net cash provided by operating activities.*?(?:fiscal year|year) {fy_year}.*?RMB\s*([\d.]+)\s*(billion|million)',
            ]
            for pattern in fy_ocf_patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).lower()
                    fy_metrics['OperatingCashFlow'] = value * 1000 if unit == 'billion' else value
                    break

            if fy_metrics:
                results.append({'Period': fy_period, **fy_metrics})

    return results

def sort_period(period_str):
    """Sort key for periods like 'Q1 2015', 'Q4 2024', 'FY2024'"""
    if period_str.startswith('FY'):
        year = int(period_str[2:])
        return (year, 5)  # FY comes after Q4
    else:
        parts = period_str.split()
        quarter = int(parts[0][1])
        year = int(parts[1])
        return (year, quarter)

def main():
    # Get all text files
    files = sorted(EXTRACTED_DIR.glob("9999.HK_6K_Q*.txt"))

    all_data = []

    for filepath in files:
        print(f"Processing {filepath.name}...")
        results = parse_file(filepath)
        all_data.extend(results)

    # Sort by period
    all_data.sort(key=lambda x: sort_period(x['Period']))

    # Define CSV columns
    columns = [
        'Period', 'Revenue', 'GrossProfit', 'GrossMargin', 'OperatingIncome', 'OperatingMargin',
        'EBITDA', 'NetIncome', 'NetMargin', 'EPS', 'FreeCashFlow', 'ShareholdersEquity',
        'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding', 'GameRevenue', 'YoudaoRevenue',
        'CloudMusicRevenue', 'InnovativeRevenue'
    ]

    # Write to CSV
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for row in all_data:
            # Fill missing columns with empty strings
            output_row = {col: row.get(col, '') for col in columns}
            # Format numeric values to 1 decimal place
            for col in columns[1:]:  # Skip Period
                if col in output_row and output_row[col] != '':
                    try:
                        output_row[col] = f"{float(output_row[col]):.1f}"
                    except:
                        pass
            writer.writerow(output_row)

    print(f"\nSuccessfully wrote {len(all_data)} records to {OUTPUT_CSV}")
    print(f"Periods: {all_data[0]['Period']} to {all_data[-1]['Period']}")

if __name__ == '__main__':
    main()
