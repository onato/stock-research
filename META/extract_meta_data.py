#!/usr/bin/env python3
"""
Extract Meta financial metrics from SEC filings.
Reads extracted text files and parses financial data.
"""

import re
import csv
from pathlib import Path
from typing import Dict, Optional, List, Tuple

# Paths
BASE_DIR = Path("/Users/swilliams/Stocks/Research/META")
EXTRACTED_DIR = BASE_DIR / "Extracted"
REPORTS_DIR = BASE_DIR / "Reports"
OUTPUT_CSV = REPORTS_DIR / "META_Metrics.csv"

# Create Reports directory if needed
REPORTS_DIR.mkdir(exist_ok=True)


def clean_number(text: str) -> Optional[float]:
    """Clean and convert a number string to float."""
    if not text:
        return None
    # Remove $, commas, parentheses, whitespace
    text = text.replace('$', '').replace(',', '').replace('(', '-').replace(')', '').strip()
    try:
        return float(text)
    except (ValueError, AttributeError):
        return None


def read_file_content(filepath: Path, start_line: int = 0, max_lines: int = 100000) -> str:
    """Read file content with line limits."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()[start_line:start_line + max_lines]
            return ''.join(lines)
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return ""


def extract_period(filename: str) -> str:
    """Extract period from filename."""
    if '10K' in filename:
        match = re.search(r'FY(\d{4})', filename)
        if match:
            return f"FY{match.group(1)}"
    elif '10Q' in filename:
        match = re.search(r'(Q\d)-(\d{4})', filename)
        if match:
            return f"{match.group(1)} {match.group(2)}"
    return "Unknown"


def extract_quarterly_income_statement(content: str, period: str) -> Dict:
    """Extract income statement data for quarterly reports (3-month period)."""
    data = {}

    # Determine which quarter and year
    if 'Q' not in period:
        return data

    quarter, year = period.split()

    # Map quarter to month
    month_map = {'Q1': 'March 31', 'Q2': 'June 30', 'Q3': 'September 30', 'Q4': 'December 31'}
    month_end = month_map.get(quarter, '')

    # Find the income statement section with "Three Months Ended"
    pattern = rf'Three Months Ended {month_end},?\s*\n.*?{year}.*?\n.*?Revenue.*?\$\s*([\d,]+)'
    match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)

    if match:
        data['Revenue'] = clean_number(match.group(1))

        # Find the surrounding context to get other metrics
        # Get larger chunk around revenue line
        start_pos = match.start()
        end_pos = min(start_pos + 3000, len(content))
        chunk = content[start_pos:end_pos]

        # Cost of revenue
        cost_match = re.search(r'Cost of revenue.*?\$\s*([\d,]+)', chunk, re.IGNORECASE)
        if cost_match:
            cost = clean_number(cost_match.group(1))
            if cost and data['Revenue']:
                data['GrossProfit'] = data['Revenue'] - cost
                data['GrossMargin'] = round((data['GrossProfit'] / data['Revenue']) * 100, 1)

        # Operating income
        op_income_match = re.search(r'Income from operations.*?\$\s*([\d,]+)', chunk, re.IGNORECASE)
        if op_income_match:
            data['OperatingIncome'] = clean_number(op_income_match.group(1))
            if data.get('Revenue'):
                data['OperatingMargin'] = round((data['OperatingIncome'] / data['Revenue']) * 100, 1)

        # Net income
        net_income_match = re.search(r'Net income.*?\$\s*([\d,]+)', chunk, re.IGNORECASE)
        if net_income_match:
            data['NetIncome'] = clean_number(net_income_match.group(1))
            if data.get('Revenue'):
                data['NetMargin'] = round((data['NetIncome'] / data['Revenue']) * 100, 1)

        # Diluted EPS
        eps_match = re.search(r'Diluted.*?\$\s*([\d.]+)', chunk, re.IGNORECASE)
        if eps_match:
            data['EPS'] = clean_number(eps_match.group(1))

        # Diluted shares
        shares_match = re.search(r'Diluted.*?\n.*?([\d,]+)', chunk, re.IGNORECASE)
        if shares_match:
            data['SharesOutstanding'] = clean_number(shares_match.group(1))

    return data


def extract_annual_income_statement(content: str, period: str) -> Dict:
    """Extract income statement data for annual reports."""
    data = {}

    if 'FY' not in period:
        return data

    year = period.replace('FY', '')

    # Find income statement section for year ended December 31
    pattern = rf'Year[s]? Ended December 31,?\s*\n.*?{year}.*?\n.*?Revenue.*?\$\s*([\d,]+)'
    match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)

    if match:
        data['Revenue'] = clean_number(match.group(1))

        # Get surrounding context
        start_pos = match.start()
        end_pos = min(start_pos + 3000, len(content))
        chunk = content[start_pos:end_pos]

        # Cost of revenue
        cost_match = re.search(r'Cost of revenue.*?\$\s*([\d,]+)', chunk, re.IGNORECASE)
        if cost_match:
            cost = clean_number(cost_match.group(1))
            if cost and data['Revenue']:
                data['GrossProfit'] = data['Revenue'] - cost
                data['GrossMargin'] = round((data['GrossProfit'] / data['Revenue']) * 100, 1)

        # Operating income
        op_income_match = re.search(r'Income from operations.*?\$\s*([\d,]+)', chunk, re.IGNORECASE)
        if op_income_match:
            data['OperatingIncome'] = clean_number(op_income_match.group(1))
            if data.get('Revenue'):
                data['OperatingMargin'] = round((data['OperatingIncome'] / data['Revenue']) * 100, 1)

        # Net income
        net_income_match = re.search(r'Net income.*?\$\s*([\d,]+)', chunk, re.IGNORECASE)
        if net_income_match:
            data['NetIncome'] = clean_number(net_income_match.group(1))
            if data.get('Revenue'):
                data['NetMargin'] = round((data['NetIncome'] / data['Revenue']) * 100, 1)

        # Diluted EPS
        eps_match = re.search(r'Diluted.*?\$\s*([\d.]+)', chunk, re.IGNORECASE)
        if eps_match:
            data['EPS'] = clean_number(eps_match.group(1))

        # Diluted shares
        shares_match = re.search(r'Diluted.*?\n.*?([\d,]+)', chunk, re.IGNORECASE)
        if shares_match:
            data['SharesOutstanding'] = clean_number(shares_match.group(1))

    return data


def extract_balance_sheet(content: str, period: str) -> Dict:
    """Extract balance sheet data."""
    data = {}

    # Look for the most recent balance sheet (first date column)
    # Cash and cash equivalents
    cash_match = re.search(r'Cash and cash equivalents.*?\$\s*([\d,]+)', content, re.IGNORECASE)
    if cash_match:
        cash = clean_number(cash_match.group(1))

        # Marketable securities
        securities_match = re.search(r'Marketable securities.*?\$\s*([\d,]+)', content, re.IGNORECASE)
        if securities_match:
            securities = clean_number(securities_match.group(1))
            if cash is not None and securities is not None:
                data['CashAndEquivalents'] = cash + securities

    # Long-term debt
    debt_match = re.search(r'Long-term debt.*?\$\s*([\d,]+)', content, re.IGNORECASE)
    if debt_match:
        data['TotalDebt'] = clean_number(debt_match.group(1))

    # Stockholders' equity
    equity_match = re.search(r'Total stockholders[\''] equity.*?\$\s*([\d,]+)', content, re.IGNORECASE)
    if equity_match:
        data['ShareholdersEquity'] = clean_number(equity_match.group(1))

    return data


def extract_cash_flow(content: str, period: str) -> Dict:
    """Extract cash flow statement data."""
    data = {}

    # Determine if quarterly or annual
    is_quarterly = 'Q' in period

    if is_quarterly:
        quarter, year = period.split()
        month_map = {'Q1': 'March 31', 'Q2': 'June 30', 'Q3': 'September 30', 'Q4': 'December 31'}
        month_end = month_map.get(quarter, '')

        # Find cash flow section for three months
        pattern = rf'Three Months Ended {month_end},?\s*\n.*?{year}.*?Net cash provided by operating activities.*?\$\s*([\d,]+)'
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)

        if match:
            data['OperatingCashFlow'] = clean_number(match.group(1))

            # Find surrounding context for CapEx
            start_pos = match.start()
            end_pos = min(start_pos + 2000, len(content))
            chunk = content[start_pos:end_pos]

            capex_match = re.search(r'Purchases of property and equipment.*?\$\s*\(?([\d,]+)\)?', chunk, re.IGNORECASE)
            if capex_match:
                data['CapEx'] = clean_number(capex_match.group(1))

                if data.get('OperatingCashFlow') and data.get('CapEx'):
                    data['FreeCashFlow'] = data['OperatingCashFlow'] - data['CapEx']

    else:
        # Annual report
        year = period.replace('FY', '')
        pattern = rf'Year[s]? Ended December 31,?\s*\n.*?{year}.*?Net cash provided by operating activities.*?\$\s*([\d,]+)'
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)

        if match:
            data['OperatingCashFlow'] = clean_number(match.group(1))

            # Find CapEx
            start_pos = match.start()
            end_pos = min(start_pos + 2000, len(content))
            chunk = content[start_pos:end_pos]

            capex_match = re.search(r'Purchases of property and equipment.*?\$\s*\(?([\d,]+)\)?', chunk, re.IGNORECASE)
            if capex_match:
                data['CapEx'] = clean_number(capex_match.group(1))

                if data.get('OperatingCashFlow') and data.get('CapEx'):
                    data['FreeCashFlow'] = data['OperatingCashFlow'] - data['CapEx']

    return data


def extract_segment_data(content: str, period: str) -> Dict:
    """Extract segment reporting (FoA and Reality Labs)."""
    data = {}

    # Look for segment results table
    # This format started around 2021-2022
    segment_pattern = r'Family of Apps.*?Reality Labs.*?Revenue.*?\$\s*([\d,]+).*?\$\s*([\d,]+)'
    match = re.search(segment_pattern, content, re.DOTALL | re.IGNORECASE)

    if match:
        foa_revenue = clean_number(match.group(1))
        rl_revenue = clean_number(match.group(2))

        # Ad revenue is approximately FoA revenue
        if foa_revenue:
            data['AdRevenue'] = foa_revenue
        if rl_revenue:
            data['RealityLabsRevenue'] = rl_revenue

        # Look for operating income/loss
        income_pattern = r'Income \(loss\) from operations.*?\$\s*([\d,]+).*?\$\s*\(?\s*([\d,]+)\s*\)?'
        income_match = re.search(income_pattern, content, re.DOTALL | re.IGNORECASE)
        if income_match:
            data['FamilyOfAppsIncome'] = clean_number(income_match.group(1))
            data['RealityLabsLoss'] = clean_number(income_match.group(2))

    # If no segment data (earlier years), ad revenue ≈ total revenue
    elif not data.get('AdRevenue'):
        # Before Reality Labs segment reporting, essentially all revenue was ads
        data['AdRevenue'] = data.get('Revenue')

    return data


def extract_user_metrics(content: str, period: str) -> Dict:
    """Extract user metrics (DAP, DAU, MAU, ARPP)."""
    data = {}

    # Family DAP (daily active people) - in billions, convert to millions
    dap_pattern = r'(?:Family\s+)?daily active people.*?(?:DAP).*?was\s+([\d.]+)\s+billion'
    match = re.search(dap_pattern, content, re.IGNORECASE | re.DOTALL)
    if match:
        data['FamilyDAP'] = clean_number(match.group(1)) * 1000  # Convert to millions

    # Family MAP/MAPE (monthly active people)
    map_pattern = r'(?:Family\s+)?monthly active people.*?(?:MAP|MAPE).*?was\s+([\d.]+)\s+billion'
    match = re.search(map_pattern, content, re.IGNORECASE | re.DOTALL)
    if match:
        data['FamilyMAPE'] = clean_number(match.group(1)) * 1000  # Convert to millions

    # Facebook DAU
    fb_dau_pattern = r'Facebook\s+daily active users.*?(?:DAU|DAUs).*?was\s+([\d.]+)\s+(billion|million)'
    match = re.search(fb_dau_pattern, content, re.IGNORECASE | re.DOTALL)
    if match:
        value = clean_number(match.group(1))
        if value:
            if 'billion' in match.group(2).lower():
                data['FacebookDAU'] = value * 1000
            else:
                data['FacebookDAU'] = value

    # Facebook MAU
    fb_mau_pattern = r'Facebook\s+monthly active users.*?(?:MAU|MAUs).*?was\s+([\d.]+)\s+(billion|million)'
    match = re.search(fb_mau_pattern, content, re.IGNORECASE | re.DOTALL)
    if match:
        value = clean_number(match.group(1))
        if value:
            if 'billion' in match.group(2).lower():
                data['FacebookMAU'] = value * 1000
            else:
                data['FacebookMAU'] = value

    # ARPP (Average Revenue Per Person)
    arpp_pattern = r'average revenue per (?:person|user).*?(?:ARPP).*?was?\s*\$\s*([\d.]+)'
    match = re.search(arpp_pattern, content, re.IGNORECASE | re.DOTALL)
    if match:
        data['ARPP'] = clean_number(match.group(1))

    return data


def extract_metrics_from_file(filepath: Path) -> Dict:
    """Extract all metrics from a single file."""
    period = extract_period(filepath.name)
    print(f"Processing: {filepath.name} -> {period}")

    # Read file content
    content = read_file_content(filepath)

    metrics = {'Period': period}

    # Extract from different sections
    if 'Q' in period:
        income_data = extract_quarterly_income_statement(content, period)
    else:
        income_data = extract_annual_income_statement(content, period)

    balance_data = extract_balance_sheet(content, period)
    cashflow_data = extract_cash_flow(content, period)
    segment_data = extract_segment_data(content, period)
    user_data = extract_user_metrics(content, period)

    # Merge all data
    metrics.update(income_data)
    metrics.update(balance_data)
    metrics.update(cashflow_data)
    metrics.update(segment_data)
    metrics.update(user_data)

    return metrics


def sort_periods(metrics_list: List[Dict]) -> List[Dict]:
    """Sort metrics chronologically."""
    def period_key(m):
        period = m['Period']
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 5, 0)  # Sort annual after Q4
        elif period.startswith('Q'):
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter, 0)
        return (0, 0, 0)

    return sorted(metrics_list, key=period_key)


def write_to_csv(metrics_list: List[Dict]):
    """Write all metrics to CSV."""
    columns = [
        'Period', 'Revenue', 'GrossProfit', 'GrossMargin', 'OperatingIncome',
        'OperatingMargin', 'NetIncome', 'NetMargin', 'EPS', 'FreeCashFlow',
        'FamilyDAP', 'FamilyMAPE', 'FacebookDAU', 'FacebookMAU', 'ARPP',
        'AdRevenue', 'RealityLabsRevenue', 'RealityLabsLoss', 'FamilyOfAppsIncome',
        'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents',
        'SharesOutstanding', 'CapEx', 'OperatingCashFlow'
    ]

    sorted_metrics = sort_periods(metrics_list)

    with open(OUTPUT_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for metrics in sorted_metrics:
            row = {col: metrics.get(col, '') for col in columns}
            writer.writerow(row)

    print(f"\n✓ Wrote {len(sorted_metrics)} periods to {OUTPUT_CSV}")
    return sorted_metrics


def main():
    """Main extraction process."""
    print("Meta Platforms Financial Metrics Extraction")
    print("=" * 70)

    # Get all files
    files = sorted(EXTRACTED_DIR.glob("META_10*.txt"))
    print(f"Found {len(files)} files to process\n")

    all_metrics = []
    for filepath in files:
        metrics = extract_metrics_from_file(filepath)
        all_metrics.append(metrics)

    # Write to CSV
    sorted_metrics = write_to_csv(all_metrics)

    # Print summary
    print("\nSummary:")
    print(f"Total periods: {len(sorted_metrics)}")
    print(f"Date range: {sorted_metrics[0]['Period']} to {sorted_metrics[-1]['Period']}")

    # Check data completeness
    complete_periods = sum(1 for m in sorted_metrics if m.get('Revenue'))
    print(f"Periods with revenue data: {complete_periods}/{len(sorted_metrics)}")

    print("\nExtraction complete!")


if __name__ == "__main__":
    main()
