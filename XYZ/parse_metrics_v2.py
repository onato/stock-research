#!/usr/bin/env python3
"""
Parse Block, Inc. (XYZ) financial metrics from SEC filings.
Handles single-line formatted extracted text files.
"""

import re
import csv
from pathlib import Path
from typing import Dict, List, Optional

# Directories
EXTRACTED_DIR = Path("/Users/swilliams/Stocks/Research/XYZ/Extracted")
OUTPUT_DIR = Path("/Users/swilliams/Stocks/Research/XYZ/Reports")
OUTPUT_FILE = OUTPUT_DIR / "XYZ_Metrics.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def clean_number(text: str) -> Optional[float]:
    """Convert text to number, handling various formats."""
    if not text:
        return None

    # Remove common non-numeric characters
    text = text.replace('$', '').replace(',', '').replace(' ', '').strip()

    # Handle parentheses as negative
    is_negative = False
    if '(' in text and ')' in text:
        is_negative = True
        text = text.replace('(', '').replace(')', '')

    try:
        num = float(text)
        return -abs(num) if is_negative else num
    except (ValueError, AttributeError):
        return None


def millions_to_number(text_amount: str, unit_hint: str = None) -> Optional[float]:
    """
    Convert amounts like '1,234' with context 'thousands' to millions.
    Most SEC filings express amounts in thousands.
    """
    num = clean_number(text_amount)
    if num is None:
        return None

    # If explicitly in thousands, convert to millions
    if unit_hint and 'thousand' in unit_hint.lower():
        return round(num / 1000, 1)

    # If in millions already
    if unit_hint and 'million' in unit_hint.lower():
        return round(num, 1)

    # Default: assume thousands (standard for SEC filings)
    return round(num / 1000, 1)


def extract_period(filename: str) -> str:
    """Extract period from filename."""
    # XYZ_10K_FY2024.txt -> FY2024
    # XYZ_10Q_Q1-2024.txt -> Q1 2024
    match = re.search(r'_(FY\d{4}|Q\d-\d{4})', filename)
    if match:
        period = match.group(1)
        if period.startswith('Q'):
            return period.replace('-', ' ')
        return period
    return None


def find_table_section(content: str, start_marker: str, length: int = 15000) -> str:
    """Find a section starting with marker and return substring."""
    match = re.search(start_marker, content, re.IGNORECASE)
    if match:
        start_pos = match.start()
        return content[start_pos:start_pos + length]
    return ""


def extract_value_after_label(content: str, label: str, position: int = 1) -> Optional[float]:
    """
    Extract a numeric value appearing after a label.
    position: 1 for first number, 2 for second, etc.
    """
    # Build pattern: label followed by numbers
    # Pattern finds label, then captures numbers (with commas, parens, $)
    pattern = rf'{re.escape(label)}\s*[\$\s]*([\d,\(\)]+(?:\.\d+)?)'

    matches = re.finditer(pattern, content, re.IGNORECASE)
    match_list = list(matches)

    if len(match_list) >= position:
        num_text = match_list[position - 1].group(1)
        return clean_number(num_text)

    return None


def extract_financial_row(content: str, label: str, num_values: int = 3) -> List[Optional[float]]:
    """
    Extract a row of financial data (typically 3 values for 3 years/quarters).
    Returns list of numbers in order they appear.
    """
    # Find the label
    label_pattern = re.escape(label)
    match = re.search(label_pattern, content, re.IGNORECASE)

    if not match:
        return [None] * num_values

    # From label position, extract next num_values numbers
    start_pos = match.end()
    extract_section = content[start_pos:start_pos + 500]  # Look ahead 500 chars

    # Find all numbers (including negatives with parens)
    number_pattern = r'[\(\$]?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*[\)]?'
    matches = re.findall(number_pattern, extract_section)

    values = []
    for match_text in matches[:num_values]:
        val = clean_number(match_text)
        values.append(val)

    # Pad with None if not enough values found
    while len(values) < num_values:
        values.append(None)

    return values[:num_values]


def parse_filing(filepath: Path, is_annual: bool = True) -> Dict[str, any]:
    """Parse a 10-K or 10-Q filing."""
    print(f"Parsing {filepath.name}...")

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    period = extract_period(filepath.name)
    if not period:
        print(f"  WARNING: Could not extract period from {filepath.name}")
        return {}

    metrics = {'Period': period}

    # Determine if amounts are in thousands or millions
    # Look for "in thousands" or "in millions" near the financial statements
    unit_hint = "thousands"  # Default for SEC filings
    if re.search(r'in millions', content[:50000], re.IGNORECASE):
        unit_hint = "millions"
    elif re.search(r'in thousands', content[:50000], re.IGNORECASE):
        unit_hint = "thousands"

    # === CONSOLIDATED STATEMENTS OF OPERATIONS ===
    ops_section = find_table_section(content, r'CONSOLIDATED STATEMENTS OF OPERATIONS', 20000)

    if ops_section:
        # Revenue components (use first column for current period)
        # Look for patterns like:
        # Transaction-based revenue   1,234   1,123   1,012

        transaction_vals = extract_financial_row(ops_section, 'Transaction-based revenue', 3)
        metrics['TransactionRevenue'] = millions_to_number(str(transaction_vals[0]), unit_hint) if transaction_vals[0] else None

        subscription_vals = extract_financial_row(ops_section, 'Subscription and services-based revenue', 3)
        metrics['SubscriptionRevenue'] = millions_to_number(str(subscription_vals[0]), unit_hint) if subscription_vals[0] else None

        hardware_vals = extract_financial_row(ops_section, 'Hardware revenue', 3)
        metrics['HardwareRevenue'] = millions_to_number(str(hardware_vals[0]), unit_hint) if hardware_vals[0] else None

        bitcoin_vals = extract_financial_row(ops_section, 'Bitcoin revenue', 3)
        metrics['BitcoinRevenue'] = millions_to_number(str(bitcoin_vals[0]), unit_hint) if bitcoin_vals[0] else None

        # Total revenue
        revenue_vals = extract_financial_row(ops_section, 'Total net revenue', 3)
        metrics['Revenue'] = millions_to_number(str(revenue_vals[0]), unit_hint) if revenue_vals[0] else None

        # Gross profit
        gross_profit_vals = extract_financial_row(ops_section, 'Gross profit', 3)
        metrics['GrossProfit'] = millions_to_number(str(gross_profit_vals[0]), unit_hint) if gross_profit_vals[0] else None

        # Calculate gross margin
        if metrics.get('Revenue') and metrics.get('GrossProfit'):
            metrics['GrossMargin'] = round((metrics['GrossProfit'] / metrics['Revenue']) * 100, 1)

        # Operating income/loss
        op_income_vals = extract_financial_row(ops_section, 'Operating income', 3)
        if not op_income_vals[0]:
            # Try "Operating loss"
            op_loss_vals = extract_financial_row(ops_section, 'Operating loss', 3)
            if op_loss_vals[0]:
                metrics['OperatingIncome'] = -millions_to_number(str(op_loss_vals[0]), unit_hint)
        else:
            metrics['OperatingIncome'] = millions_to_number(str(op_income_vals[0]), unit_hint)

        # Operating margin
        if metrics.get('Revenue') and metrics.get('OperatingIncome'):
            metrics['OperatingMargin'] = round((metrics['OperatingIncome'] / metrics['Revenue']) * 100, 1)

        # Net income/loss
        net_income_vals = extract_financial_row(ops_section, 'Net income', 3)
        if not net_income_vals[0]:
            # Try "Net loss"
            net_loss_vals = extract_financial_row(ops_section, 'Net loss', 3)
            if net_loss_vals[0]:
                metrics['NetIncome'] = -millions_to_number(str(net_loss_vals[0]), unit_hint)
        else:
            metrics['NetIncome'] = millions_to_number(str(net_income_vals[0]), unit_hint)

        # Net margin
        if metrics.get('Revenue') and metrics.get('NetIncome'):
            metrics['NetMargin'] = round((metrics['NetIncome'] / metrics['Revenue']) * 100, 1)

        # EPS (diluted) - this is already per share, don't convert
        eps_pattern = r'(?:Diluted|Net (?:income|loss) per share.*diluted).*?([\(\$]?\s*\d+\.\d+\s*[\)]?)'
        eps_match = re.search(eps_pattern, ops_section, re.IGNORECASE)
        if eps_match:
            eps_text = eps_match.group(1)
            metrics['EPS'] = clean_number(eps_text)

    # === CONSOLIDATED BALANCE SHEETS ===
    bs_section = find_table_section(content, r'CONSOLIDATED BALANCE SHEETS', 20000)

    if bs_section:
        # Cash and equivalents (use first column - current period)
        cash_vals = extract_financial_row(bs_section, 'Cash and cash equivalents', 2)
        cash_amt = millions_to_number(str(cash_vals[0]), unit_hint) if cash_vals[0] else None

        # Short-term investments
        invest_vals = extract_financial_row(bs_section, 'Short-term investments', 2)
        invest_amt = millions_to_number(str(invest_vals[0]), unit_hint) if invest_vals[0] else None

        # Combine cash + investments
        if cash_amt and invest_amt:
            metrics['CashAndEquivalents'] = cash_amt + invest_amt
        elif cash_amt:
            metrics['CashAndEquivalents'] = cash_amt

        # Total debt (look for long-term debt primarily)
        lt_debt_vals = extract_financial_row(bs_section, 'Long-term debt', 2)
        st_debt_vals = extract_financial_row(bs_section, 'Short-term debt', 2)

        lt_debt = millions_to_number(str(lt_debt_vals[0]), unit_hint) if lt_debt_vals[0] else 0
        st_debt = millions_to_number(str(st_debt_vals[0]), unit_hint) if st_debt_vals[0] else 0

        if lt_debt or st_debt:
            metrics['TotalDebt'] = (lt_debt or 0) + (st_debt or 0)

        # Shareholders' equity
        equity_vals = extract_financial_row(bs_section, 'Total stockholders.*equity', 2)
        metrics['ShareholdersEquity'] = millions_to_number(str(equity_vals[0]), unit_hint) if equity_vals[0] else None

        # Shares outstanding (in millions already, don't convert)
        shares_pattern = r'(?:Shares outstanding|Common stock.*outstanding).*?(\d+(?:\.\d+)?)'
        shares_match = re.search(shares_pattern, bs_section, re.IGNORECASE)
        if shares_match:
            metrics['SharesOutstanding'] = clean_number(shares_match.group(1))

    # === CONSOLIDATED STATEMENTS OF CASH FLOWS ===
    cf_section = find_table_section(content, r'CONSOLIDATED STATEMENTS OF CASH FLOWS', 20000)

    if cf_section:
        # Operating cash flow
        ocf_pattern = r'Net cash (?:provided by|used in) operating activities.*?(\d{1,3}(?:,\d{3})*)'
        ocf_match = re.search(ocf_pattern, cf_section, re.IGNORECASE)
        if ocf_match:
            metrics['OperatingCashFlow'] = millions_to_number(ocf_match.group(1), unit_hint)

        # Capital expenditures
        capex_pattern = r'Purchase(?:s)? of property and equipment.*?[\(\$]?\s*(\d{1,3}(?:,\d{3})*)\s*[\)]?'
        capex_match = re.search(capex_pattern, cf_section, re.IGNORECASE)
        if capex_match:
            capex_val = millions_to_number(capex_match.group(1), unit_hint)
            metrics['CapitalExpenditures'] = abs(capex_val) if capex_val else None

            # Calculate FCF
            if metrics.get('OperatingCashFlow') and metrics.get('CapitalExpenditures'):
                metrics['FreeCashFlow'] = metrics['OperatingCashFlow'] - metrics['CapitalExpenditures']

    # === KPIs (from MD&A sections) ===
    # Look for Gross Payment Volume
    gpv_pattern = r'(?:Square|Seller).*?(?:GPV|gross payment volume).*?(\d{1,3}(?:\.\d+)?)\s*billion'
    gpv_match = re.search(gpv_pattern, content, re.IGNORECASE)
    if gpv_match:
        # Convert billions to millions
        gpv_billions = float(gpv_match.group(1))
        metrics['SquareGPV'] = gpv_billions * 1000

    # Cash App Inflows
    inflow_pattern = r'Cash App.*?(?:inflow|volume).*?(\d{1,3}(?:\.\d+)?)\s*billion'
    inflow_match = re.search(inflow_pattern, content, re.IGNORECASE)
    if inflow_match:
        inflow_billions = float(inflow_match.group(1))
        metrics['CashAppInflows'] = inflow_billions * 1000

    # Monthly Active Users
    ca_actives_pattern = r'Cash App.*?monthly.*?active.*?(\d{1,3}(?:\.\d+)?)\s*million'
    ca_actives_match = re.search(ca_actives_pattern, content, re.IGNORECASE)
    if ca_actives_match:
        metrics['CashAppMonthlyActives'] = float(ca_actives_match.group(1))

    sq_actives_pattern = r'Square.*?(?:seller|monthly).*?active.*?(\d{1,3}(?:\.\d+)?)\s*million'
    sq_actives_match = re.search(sq_actives_pattern, content, re.IGNORECASE)
    if sq_actives_match:
        metrics['SquareMonthlyActives'] = float(sq_actives_match.group(1))

    # Debug output
    print(f"  Period: {period}")
    print(f"  Revenue: {metrics.get('Revenue')}, Net Income: {metrics.get('NetIncome')}")

    return metrics


def sort_periods(data: List[Dict]) -> List[Dict]:
    """Sort periods chronologically."""
    def get_sort_key(item):
        period = item.get('Period', '')
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 5, 0)  # FY after Q4
        elif period.startswith('Q'):
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter, 0)
        return (0, 0, 0)

    return sorted(data, key=get_sort_key)


def main():
    """Main execution."""
    print("=" * 70)
    print("Block, Inc. (XYZ) Financial Metrics Parser")
    print("=" * 70)

    all_data = []

    # Parse 10-K files
    print("\nParsing Annual Reports (10-K)...")
    for filepath in sorted(EXTRACTED_DIR.glob("XYZ_10K_*.txt")):
        try:
            metrics = parse_filing(filepath, is_annual=True)
            if metrics.get('Period'):
                all_data.append(metrics)
        except Exception as e:
            print(f"  ERROR: {e}")

    # Parse 10-Q files
    print("\nParsing Quarterly Reports (10-Q)...")
    for filepath in sorted(EXTRACTED_DIR.glob("XYZ_10Q_*.txt")):
        try:
            metrics = parse_filing(filepath, is_annual=False)
            if metrics.get('Period'):
                all_data.append(metrics)
        except Exception as e:
            print(f"  ERROR: {e}")

    # Sort chronologically
    all_data = sort_periods(all_data)

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

    # Write CSV
    print(f"\n{'=' * 70}")
    print(f"Writing {len(all_data)} periods to CSV...")

    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for row_data in all_data:
            row = {col: row_data.get(col, '') for col in columns}
            # Convert None to empty string
            row = {k: (v if v is not None else '') for k, v in row.items()}
            writer.writerow(row)

    print(f"Done! File saved to:")
    print(f"  {OUTPUT_FILE}")
    print(f"\nTotal periods extracted: {len(all_data)}")

    # Show sample of most recent periods
    if all_data:
        print(f"\nMost recent 5 periods:")
        for item in all_data[-5:]:
            rev = item.get('Revenue', 'N/A')
            ni = item.get('NetIncome', 'N/A')
            print(f"  {item['Period']:12s} Revenue: ${rev:>8}M  Net Income: ${ni:>8}M")

    print("\n" + "=" * 70)


if __name__ == '__main__':
    main()
