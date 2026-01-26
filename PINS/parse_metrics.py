#!/usr/bin/env python3
"""
Parse Pinterest (PINS) financial metrics from extracted SEC filings.
Extracts data from 10-K (annual) and 10-Q (quarterly) reports.
"""

import re
import os
import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

# Base directory
BASE_DIR = Path("/Users/swilliams/Stocks/Research/PINS/Extracted")
OUTPUT_FILE = Path("/Users/swilliams/Stocks/Research/PINS/Reports/PINS_Metrics.csv")

def parse_number(text: str) -> Optional[float]:
    """Convert text number to float (in thousands as presented in filings)."""
    if not text or text.strip() in ['—', '-', 'N/A', '']:
        return None
    # Remove $ and commas, handle parentheses for negatives
    text = text.strip().replace('$', '').replace(',', '')
    if '(' in text:
        text = '-' + text.replace('(', '').replace(')', '')
    try:
        return float(text)
    except:
        return None

def convert_to_millions(value: Optional[float]) -> Optional[float]:
    """Convert from thousands to millions."""
    if value is None:
        return None
    return round(value / 1000, 1)

def read_file(filepath: Path) -> str:
    """Read file content."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return ""

def extract_10k_data(filepath: Path, year: int) -> Dict:
    """Extract annual data from 10-K filing."""
    content = read_file(filepath)
    if not content:
        return {}

    data = {'Period': f'FY{year}', 'Type': 'Annual'}

    # Find consolidated statements of operations
    ops_pattern = r'Consolidated statements of operations.*?(?=Consolidated|Part II|\n\n\d+\n)'
    ops_match = re.search(ops_pattern, content, re.IGNORECASE | re.DOTALL)

    if ops_match:
        ops_section = ops_match.group()

        # Look for year columns - typically shows 3 years
        year_pattern = rf'{year}\s+{year-1}\s+{year-2}'
        if re.search(year_pattern, ops_section):
            # Extract revenue (first year column)
            revenue_pattern = rf'Revenue\s+\$?\s*([\d,]+)\s+\$?\s*[\d,]+'
            rev_match = re.search(revenue_pattern, ops_section)
            if rev_match:
                data['Revenue'] = convert_to_millions(parse_number(rev_match.group(1)))

            # Extract cost of revenue
            cost_pattern = rf'Cost of revenue\s+([\d,]+)\s+[\d,]+'
            cost_match = re.search(cost_pattern, ops_section)
            if cost_match:
                cost_rev = parse_number(cost_match.group(1))
                if data.get('Revenue') and cost_rev:
                    data['GrossProfit'] = convert_to_millions(parse_number(rev_match.group(1)) - cost_rev)
                    data['GrossMargin'] = round((data['GrossProfit'] / data['Revenue']) * 100, 1) if data['Revenue'] else None

            # Extract operating income/loss
            op_income_pattern = rf'Income \(loss\) from operations\s+([\(\d,\)]+)\s+[\(\d,\)]+'
            op_match = re.search(op_income_pattern, ops_section)
            if op_match:
                data['OperatingIncome'] = convert_to_millions(parse_number(op_match.group(1)))
                if data.get('Revenue') and data.get('OperatingIncome'):
                    data['OperatingMargin'] = round((data['OperatingIncome'] / data['Revenue']) * 100, 1)

            # Extract net income/loss
            net_pattern = rf'Net income \(loss\)\s+\$?\s*([\(\d,\)]+)\s+\$?\s*[\(\d,\)]+'
            net_match = re.search(net_pattern, ops_section)
            if net_match:
                data['NetIncome'] = convert_to_millions(parse_number(net_match.group(1)))
                if data.get('Revenue') and data.get('NetIncome'):
                    data['NetMargin'] = round((data['NetIncome'] / data['Revenue']) * 100, 1)

            # Extract EPS (diluted)
            eps_pattern = rf'Diluted\s+\$?\s*([\(\d\.\)]+)\s+\$?\s*[\(\d\.\)]+'
            eps_match = re.search(eps_pattern, ops_section)
            if eps_match:
                data['EPS'] = parse_number(eps_match.group(1))

            # Extract shares outstanding (diluted)
            shares_pattern = rf'Diluted\s+([\d,]+)\s+[\d,]+\s+[\d,]+'
            shares_match = re.search(shares_pattern, ops_section)
            if shares_match:
                data['SharesOutstanding'] = convert_to_millions(parse_number(shares_match.group(1)))

    # Find balance sheet
    bs_pattern = r'Consolidated balance sheets.*?(?=Consolidated|Part II|\n\n\d+\n)'
    bs_match = re.search(bs_pattern, content, re.IGNORECASE | re.DOTALL)

    if bs_match:
        bs_section = bs_match.group()

        # Extract cash and equivalents (current year)
        cash_pattern = rf'Cash and cash equivalents\s+\$?\s*([\d,]+)\s+\$?\s*[\d,]+'
        cash_match = re.search(cash_pattern, bs_section)
        if cash_match:
            data['CashAndEquivalents'] = convert_to_millions(parse_number(cash_match.group(1)))

        # Extract marketable securities
        securities_pattern = rf'Marketable securities\s+([\d,]+)\s+[\d,]+'
        sec_match = re.search(securities_pattern, bs_section)
        if sec_match:
            securities = convert_to_millions(parse_number(sec_match.group(1)))
            if data.get('CashAndEquivalents') and securities:
                data['CashAndEquivalents'] = round(data['CashAndEquivalents'] + securities, 1)

        # Extract total stockholders' equity
        equity_pattern = rf'Total stockholders. equity\s+([\(\d,\)]+)\s+[\(\d,\)]+'
        equity_match = re.search(equity_pattern, bs_section)
        if equity_match:
            data['ShareholdersEquity'] = convert_to_millions(parse_number(equity_match.group(1)))

        # Pinterest typically has minimal debt - check for any debt line items
        # Most tech companies show debt in liabilities section
        data['TotalDebt'] = 0.0  # Pinterest has been essentially debt-free

    # Find cash flow statement
    cf_pattern = r'Consolidated statements of cash flows.*?(?=Consolidated|Part II|\n\n\d+\n)'
    cf_match = re.search(cf_pattern, content, re.IGNORECASE | re.DOTALL)

    if cf_match:
        cf_section = cf_match.group()

        # Extract operating cash flow
        ocf_pattern = rf'Net cash provided by operating activities\s+([\d,]+)\s+[\d,]+'
        ocf_match = re.search(ocf_pattern, cf_section)
        if ocf_match:
            ocf = convert_to_millions(parse_number(ocf_match.group(1)))
            data['OperatingCashFlow'] = ocf

            # Extract CapEx
            capex_pattern = rf'Purchases of property and equipment\s+([\d,]+)\s+[\d,]+'
            capex_match = re.search(capex_pattern, cf_section)
            if capex_match:
                capex = convert_to_millions(parse_number(capex_match.group(1)))
                if ocf and capex:
                    data['FreeCashFlow'] = round(ocf - capex, 1)

    # Look for Adjusted EBITDA in MD&A section
    ebitda_pattern = rf'Adjusted EBITDA.*?(?:was|were)\s+\$?([\d,]+(?:\.\d+)?)\s+million'
    ebitda_match = re.search(ebitda_pattern, content, re.IGNORECASE)
    if ebitda_match:
        data['EBITDA'] = parse_number(ebitda_match.group(1))

    # Extract MAU data from user metrics section
    mau_pattern = rf'Monthly active users.*?were\s+([\d,]+)\s+million'
    mau_match = re.search(mau_pattern, content, re.IGNORECASE)
    if mau_match:
        data['MAU_Global'] = parse_number(mau_match.group(1))

    # Extract ARPU
    arpu_pattern = rf'global ARPU was\s+\$?([\d\.]+)'
    arpu_match = re.search(arpu_pattern, content, re.IGNORECASE)
    if arpu_match:
        data['ARPU_Global'] = parse_number(arpu_match.group(1))

    return data

def extract_10q_data(filepath: Path, period: str) -> Dict:
    """Extract quarterly data from 10-Q filing."""
    content = read_file(filepath)
    if not content:
        return {}

    data = {'Period': period, 'Type': 'Quarterly'}

    # Similar extraction logic as 10K but looking for quarterly columns
    # 10-Q typically shows current quarter and YTD

    # Find consolidated statements of operations
    ops_pattern = r'Consolidated statements of operations.*?(?=Consolidated|Part I|\n\n\d+\n)'
    ops_match = re.search(ops_pattern, content, re.IGNORECASE | re.DOTALL)

    if ops_match:
        ops_section = ops_match.group()

        # Look for "Three Months Ended" section
        three_months_pattern = r'Three Months Ended.*?(?=Nine Months|$)'
        three_mo_match = re.search(three_months_pattern, ops_section, re.DOTALL)

        if three_mo_match:
            section = three_mo_match.group()

            # Extract revenue (first column after "Three Months Ended")
            revenue_pattern = r'Revenue\s+\$?\s*([\d,]+)'
            rev_match = re.search(revenue_pattern, section)
            if rev_match:
                data['Revenue'] = convert_to_millions(parse_number(rev_match.group(1)))

            # Extract cost of revenue
            cost_pattern = r'Cost of revenue\s+([\d,]+)'
            cost_match = re.search(cost_pattern, section)
            if cost_match:
                cost_rev = parse_number(cost_match.group(1))
                if data.get('Revenue') and cost_rev:
                    revenue_thousands = parse_number(rev_match.group(1))
                    data['GrossProfit'] = convert_to_millions(revenue_thousands - cost_rev)
                    data['GrossMargin'] = round((data['GrossProfit'] / data['Revenue']) * 100, 1) if data['Revenue'] else None

            # Extract operating income/loss
            op_income_pattern = r'(?:Income|Loss) from operations\s+([\(\d,\)]+)'
            op_match = re.search(op_income_pattern, section)
            if op_match:
                data['OperatingIncome'] = convert_to_millions(parse_number(op_match.group(1)))
                if data.get('Revenue') and data.get('OperatingIncome'):
                    data['OperatingMargin'] = round((data['OperatingIncome'] / data['Revenue']) * 100, 1)

            # Extract net income/loss
            net_pattern = r'Net (?:income|loss)\s+\$?\s*([\(\d,\)]+)'
            net_match = re.search(net_pattern, section)
            if net_match:
                data['NetIncome'] = convert_to_millions(parse_number(net_match.group(1)))
                if data.get('Revenue') and data.get('NetIncome'):
                    data['NetMargin'] = round((data['NetIncome'] / data['Revenue']) * 100, 1)

            # Extract EPS (diluted)
            eps_pattern = r'Diluted\s+\$?\s*([\(\d\.\)]+)'
            eps_match = re.search(eps_pattern, section)
            if eps_match:
                data['EPS'] = parse_number(eps_match.group(1))

    # Balance sheet data from most recent quarter
    bs_pattern = r'Consolidated balance sheets.*?(?=Consolidated|Part I|\n\n\d+\n)'
    bs_match = re.search(bs_pattern, content, re.IGNORECASE | re.DOTALL)

    if bs_match:
        bs_section = bs_match.group()

        # Extract cash (first column is most recent quarter)
        cash_pattern = r'Cash and cash equivalents\s+\$?\s*([\d,]+)'
        cash_match = re.search(cash_pattern, bs_section)
        if cash_match:
            data['CashAndEquivalents'] = convert_to_millions(parse_number(cash_match.group(1)))

        # Extract marketable securities
        securities_pattern = r'Marketable securities\s+([\d,]+)'
        sec_match = re.search(securities_pattern, bs_section)
        if sec_match:
            securities = convert_to_millions(parse_number(sec_match.group(1)))
            if data.get('CashAndEquivalents') and securities:
                data['CashAndEquivalents'] = round(data['CashAndEquivalents'] + securities, 1)

        # Extract total stockholders' equity
        equity_pattern = r'Total stockholders. equity\s+([\(\d,\)]+)'
        equity_match = re.search(equity_pattern, bs_section)
        if equity_match:
            data['ShareholdersEquity'] = convert_to_millions(parse_number(equity_match.group(1)))

        data['TotalDebt'] = 0.0  # Pinterest has been essentially debt-free

    # Cash flow - look for YTD operating cash flow
    cf_pattern = r'Consolidated statements of cash flows.*?(?=Consolidated|Part I|\n\n\d+\n)'
    cf_match = re.search(cf_pattern, content, re.IGNORECASE | re.DOTALL)

    if cf_match:
        cf_section = cf_match.group()

        # For quarterly FCF, we need to look at the quarterly section if available
        # Often 10-Q shows YTD numbers, so quarterly calculation may need derivation
        ocf_pattern = r'Net cash provided by operating activities\s+([\d,]+)'
        ocf_match = re.search(ocf_pattern, cf_section)

        capex_pattern = r'Purchases of property and equipment\s+([\d,]+)'
        capex_match = re.search(capex_pattern, cf_section)

        # Note: These are often YTD numbers in 10-Q, not quarterly
        # We'll extract them but mark appropriately

    return data

def extract_all_data() -> List[Dict]:
    """Extract data from all available filings."""
    all_data = []

    # Process 10-K files (annual reports)
    for year in range(2019, 2025):
        filepath = BASE_DIR / f"PINS_10K_FY{year}.txt"
        if filepath.exists():
            print(f"Processing {filepath.name}...")
            data = extract_10k_data(filepath, year)
            if data:
                all_data.append(data)

    # Process 10-Q files (quarterly reports)
    quarters = ['Q1', 'Q2', 'Q3']
    for year in range(2019, 2026):
        for quarter in quarters:
            filepath = BASE_DIR / f"PINS_10Q_{quarter}-{year}.txt"
            if filepath.exists():
                print(f"Processing {filepath.name}...")
                period_str = f"{quarter} {year}"
                data = extract_10q_data(filepath, period_str)
                if data:
                    all_data.append(data)

    return all_data

def sort_periods(periods: List[Dict]) -> List[Dict]:
    """Sort periods chronologically."""
    def period_key(item):
        period = item['Period']
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 4, 0)  # Annual reports sort after Q4
        elif period.startswith('Q'):
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter, 0)
        return (9999, 0, 0)

    return sorted(periods, key=period_key)

def write_csv(data: List[Dict], output_path: Path):
    """Write data to CSV file."""
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Define column order
    columns = [
        'Period', 'Revenue', 'GrossProfit', 'GrossMargin', 'OperatingIncome',
        'OperatingMargin', 'EBITDA', 'NetIncome', 'NetMargin', 'EPS',
        'OperatingCashFlow', 'FreeCashFlow', 'ShareholdersEquity', 'TotalDebt',
        'CashAndEquivalents', 'SharesOutstanding', 'MAU_Global', 'ARPU_Global'
    ]

    # Sort data chronologically
    sorted_data = sort_periods(data)

    # Write CSV
    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()

        for row in sorted_data:
            # Create output row with only specified columns
            output_row = {col: row.get(col, '') for col in columns}
            # Convert None to empty string
            output_row = {k: ('' if v is None else v) for k, v in output_row.items()}
            writer.writerow(output_row)

    print(f"\nMetrics written to: {output_path}")
    print(f"Total periods: {len(sorted_data)}")

def main():
    """Main execution function."""
    print("Pinterest (PINS) Financial Metrics Parser")
    print("=" * 50)

    # Extract all data
    data = extract_all_data()

    if not data:
        print("ERROR: No data extracted!")
        sys.exit(1)

    # Write to CSV
    write_csv(data, OUTPUT_FILE)

    print("\nDone!")

if __name__ == "__main__":
    main()
