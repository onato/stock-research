#!/usr/bin/env python3
"""
Build MSFT metrics CSV from manually reviewed data points.
This approach: manually extract key metrics from representative files,
then programmatically fill in the rest.
"""

import csv
import re
from pathlib import Path

BASE_DIR = Path("/Users/swilliams/Stocks/Research/MSFT")
EXTRACTED_DIR = BASE_DIR / "Extracted"
REPORTS_DIR = BASE_DIR / "Reports"


def extract_table_data(filepath, start_pattern, end_pattern, columns_to_extract):
    """
    Extract table data between start and end patterns.
    Returns a dict mapping column names to values.
    """
    content = filepath.read_text()
    lines = [line.strip() for line in content.split('\n')]

    # Find start of table
    start_idx = None
    for i, line in enumerate(lines):
        if re.search(start_pattern, line, re.IGNORECASE):
            start_idx = i
            break

    if start_idx is None:
        return {}

    # Find end of table
    end_idx = start_idx + 200  # Reasonable limit
    for i in range(start_idx, min(start_idx + 300, len(lines))):
        if re.search(end_pattern, line, re.IGNORECASE):
            end_idx = i
            break

    # Extract values
    table_section = lines[start_idx:end_idx]
    data = {}

    for col_name, pattern in columns_to_extract.items():
        for i, line in enumerate(table_section):
            if re.match(f'^{pattern}$', line):
                # Try to get number from next few lines
                for offset in range(1, 6):
                    if i + offset < len(table_section):
                        val_line = table_section[i + offset].replace('$', '').replace(',', '').strip()
                        # Handle parentheses for negatives
                        if val_line.startswith('(') and val_line.endswith(')'):
                            val_line = '-' + val_line[1:-1]
                        try:
                            data[col_name] = float(val_line)
                            break
                        except ValueError:
                            continue
                break

    return data


def parse_10k_file(filepath):
    """Parse a 10-K file for annual data"""
    year_match = re.search(r'FY(\d{4})', filepath.name)
    if not year_match:
        return None

    year = year_match.group(1)
    period = f"FY{year}"

    print(f"Parsing {filepath.name}...")

    # Income statement data
    income_cols = {
        'Revenue': 'Total revenue',
        'GrossProfit': 'Gross margin',
        'OperatingIncome': 'Operating income',
        'NetIncome': 'Net income',
        'EPS': 'Diluted',
    }

    income_data = extract_table_data(
        filepath,
        r'(In millions.*except per share|Year Ended June 30)',
        r'(COMPREHENSIVE|BALANCE|Refer to accompanying)',
        income_cols
    )

    # Add period
    income_data['Period'] = period

    # Calculate margins
    if income_data.get('Revenue'):
        rev = income_data['Revenue']
        if income_data.get('GrossProfit'):
            income_data['GrossMargin'] = round((income_data['GrossProfit'] / rev) * 100, 1)
        if income_data.get('OperatingIncome'):
            income_data['OperatingMargin'] = round((income_data['OperatingIncome'] / rev) * 100, 1)
        if income_data.get('NetIncome'):
            income_data['NetMargin'] = round((income_data['NetIncome'] / rev) * 100, 1)

    return income_data


def parse_10q_file(filepath):
    """Parse a 10-Q file for quarterly data"""
    match = re.search(r'Q(\d)-FY(\d{4})', filepath.name)
    if not match:
        return None

    quarter = match.group(1)
    year = match.group(2)
    period = f"Q{quarter} FY{year}"

    print(f"Parsing {filepath.name}...")

    # Income statement data (quarterly files have "Total revenue" instead of just "Revenue")
    income_cols = {
        'Revenue': 'Total revenue',
        'GrossProfit': 'Gross margin',
        'OperatingIncome': 'Operating income',
        'NetIncome': 'Net income',
        'EPS': 'Diluted',
    }

    income_data = extract_table_data(
        filepath,
        r'(INCOME STA|Three Months Ended)',
        r'(COMPREHENSIVE|BALANCE|Refer to accompanying)',
        income_cols
    )

    # Add period
    income_data['Period'] = period

    # Calculate margins
    if income_data.get('Revenue'):
        rev = income_data['Revenue']
        if income_data.get('GrossProfit'):
            income_data['GrossMargin'] = round((income_data['GrossProfit'] / rev) * 100, 1)
        if income_data.get('OperatingIncome'):
            income_data['OperatingMargin'] = round((income_data['OperatingIncome'] / rev) * 100, 1)
        if income_data.get('NetIncome'):
            income_data['NetMargin'] = round((income_data['NetIncome'] / rev) * 100, 1)

    return income_data


def main():
    """Main processing"""
    print("="*60)
    print("Microsoft Metrics CSV Builder")
    print("="*60)
    print()

    all_metrics = []

    # Parse 10-K files (annual)
    for filepath in sorted(EXTRACTED_DIR.glob("MSFT_10K_*.txt")):
        metrics = parse_10k_file(filepath)
        if metrics:
            all_metrics.append(metrics)

    # Parse 10-Q files (quarterly)
    for filepath in sorted(EXTRACTED_DIR.glob("MSFT_10Q_*.txt")):
        metrics = parse_10q_file(filepath)
        if metrics:
            all_metrics.append(metrics)

    # Sort by period
    def period_key(m):
        period = m.get('Period', '')
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 5)
        elif period.startswith('Q'):
            match = re.match(r'Q(\d) FY(\d{4})', period)
            if match:
                quarter = int(match.group(1))
                year = int(match.group(2))
                return (year, quarter)
        return (0, 0)

    all_metrics.sort(key=period_key)

    # Write CSV
    columns = [
        'Period', 'Revenue', 'GrossProfit', 'GrossMargin',
        'OperatingIncome', 'OperatingMargin', 'NetIncome', 'NetMargin',
        'EPS', 'FreeCashFlow', 'OperatingCashFlow', 'CapEx',
        'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding',
        'CloudRevenue', 'ProductivityRevenue', 'PersonalComputingRevenue'
    ]

    output_path = REPORTS_DIR / "MSFT_Metrics.csv"
    REPORTS_DIR.mkdir(exist_ok=True)

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for metrics in all_metrics:
            row = {col: metrics.get(col, '') for col in columns}
            writer.writerow(row)

    print()
    print("="*60)
    print(f"✓ Wrote {len(all_metrics)} periods to:")
    print(f"  {output_path}")
    print("="*60)
    print()

    # Show sample
    print("Recent periods:")
    for metrics in all_metrics[-5:]:
        period = metrics.get('Period', '?')
        revenue = metrics.get('Revenue', 0)
        net_income = metrics.get('NetIncome', 0)
        eps = metrics.get('EPS', 0)
        print(f"  {period:12} Rev: ${revenue:>10,.0f}M  NI: ${net_income:>10,.0f}M  EPS: ${eps:>6.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
