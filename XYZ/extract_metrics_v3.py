#!/usr/bin/env python3
"""
Block, Inc. (XYZ) Financial Metrics Extraction Script v3
Handles SEC HTML-to-text format where numbers are concatenated.
"""
import re
import csv
from pathlib import Path

EXTRACTED_DIR = Path("/Users/swilliams/Stocks/Research/XYZ/Extracted")
OUTPUT_FILE = Path("/Users/swilliams/Stocks/Research/XYZ/Reports/XYZ_Metrics.csv")
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

def clean_number(s):
    """Convert string number to float, handling parentheses for negatives."""
    if not s:
        return None
    s = str(s).replace('$', '').replace(',', '').replace(' ', '').strip()
    is_negative = '(' in s and ')' in s
    s = s.replace('(', '').replace(')', '')
    try:
        val = float(s)
        return -val if is_negative else val
    except:
        return None

def extract_first_number(text, label):
    """
    Extract the first number after a label.
    Handles SEC format where numbers are concatenated: $24,121,053$21,915,623
    """
    # Pattern to find label followed by the first complete number
    # Numbers in SEC filings are typically like $24,121,053 (with commas, no decimals for thousands)
    pattern = re.escape(label) + r'\s*\$?([\d,]+)'
    match = re.search(pattern, text, re.I)
    if match:
        return clean_number(match.group(1))
    return None

def extract_number_with_parens(text, label):
    """
    Extract a number that may be in parentheses (indicating negative).
    Pattern: label (123,456) or label $123,456
    """
    # First try with parentheses (negative)
    pattern_neg = re.escape(label) + r'\s*\(\$?([\d,]+)\)'
    match = re.search(pattern_neg, text, re.I)
    if match:
        val = clean_number(match.group(1))
        return -abs(val) if val else None

    # Then try positive
    pattern_pos = re.escape(label) + r'\s*\$?([\d,]+)'
    match = re.search(pattern_pos, text, re.I)
    if match:
        return clean_number(match.group(1))
    return None

def parse_sec_filing(filepath):
    """Parse a single SEC filing and return extracted metrics."""
    filename = filepath.name

    # Determine period from filename
    period_match = re.search(r'_(FY\d{4}|Q\d-\d{4})', filename)
    if not period_match:
        return None

    period_str = period_match.group(1)
    if period_str.startswith('FY'):
        period = period_str  # e.g., FY2024
    else:
        period = period_str.replace('-', ' ')  # e.g., Q1 2024

    # Read file content
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Initialize data dict
    data = {'Period': period}

    # Detect units - SEC filings typically in thousands
    # Look for "(In thousands" pattern near the beginning
    unit_divisor = 1000  # Default: convert thousands to millions
    if re.search(r'\(in millions', content[:100000], re.I):
        unit_divisor = 1

    # === INCOME STATEMENT METRICS ===

    # Total net revenue
    rev = extract_first_number(content, 'Total net revenue')
    if rev:
        data['Revenue'] = round(rev / unit_divisor, 1)

    # Gross profit
    gp = extract_first_number(content, 'Gross profit')
    if gp:
        data['GrossProfit'] = round(gp / unit_divisor, 1)

    # Calculate gross margin
    if data.get('Revenue') and data.get('GrossProfit'):
        data['GrossMargin'] = round(data['GrossProfit'] / data['Revenue'] * 100, 1)

    # Operating income/loss - try several patterns
    op_income = extract_number_with_parens(content, 'Operating income')
    if op_income is None:
        op_loss = extract_first_number(content, 'Operating loss')
        if op_loss:
            op_income = -abs(op_loss)
    if op_income is None:
        op_loss = extract_first_number(content, 'Loss from operations')
        if op_loss:
            op_income = -abs(op_loss)
    if op_income is not None:
        data['OperatingIncome'] = round(op_income / unit_divisor, 1)

    # Calculate operating margin
    if data.get('Revenue') and data.get('OperatingIncome') is not None:
        data['OperatingMargin'] = round(data['OperatingIncome'] / data['Revenue'] * 100, 1)

    # Net income/loss
    ni = extract_number_with_parens(content, 'Net income attributable to common stockholders')
    if ni is None:
        ni = extract_number_with_parens(content, 'Net income (loss)')
    if ni is None:
        ni = extract_number_with_parens(content, 'Net income')
    if ni is None:
        ni_loss = extract_first_number(content, 'Net loss')
        if ni_loss:
            ni = -abs(ni_loss)
    if ni is not None:
        data['NetIncome'] = round(ni / unit_divisor, 1)

    # Calculate net margin
    if data.get('Revenue') and data.get('NetIncome') is not None:
        data['NetMargin'] = round(data['NetIncome'] / data['Revenue'] * 100, 1)

    # EPS - Diluted (per share, not in thousands)
    # Look for diluted EPS pattern
    eps_patterns = [
        r'Diluted\s*\$?\(?([\d.]+)\)?',
        r'(?:Net income|Net loss) per share.{0,50}diluted\s*\$?\(?([\d.]+)\)?',
        r'diluted\s*\$?\(?([\d.]+)\)?'
    ]
    for pattern in eps_patterns:
        eps_match = re.search(pattern, content, re.I)
        if eps_match:
            eps_val = clean_number(eps_match.group(1))
            if eps_val and eps_val < 100:  # EPS should be reasonable
                # Check if it's negative (has parentheses before)
                start = eps_match.start()
                if start > 0 and '(' in content[max(0, start-5):start]:
                    eps_val = -abs(eps_val)
                data['EPS'] = round(eps_val, 2)
                break

    # === BALANCE SHEET METRICS ===

    # Stockholders' equity
    equity = extract_first_number(content, "Total stockholders' equity")
    if equity is None:
        equity = extract_first_number(content, "Stockholders' equity")
    if equity:
        data['ShareholdersEquity'] = round(equity / unit_divisor, 1)

    # Long-term debt
    debt = extract_first_number(content, 'Long-term debt')
    if debt is None:
        debt = extract_first_number(content, 'Total debt')
    if debt:
        data['TotalDebt'] = round(debt / unit_divisor, 1)

    # Cash and equivalents
    cash = extract_first_number(content, 'Cash and cash equivalents')
    if cash:
        data['CashAndEquivalents'] = round(cash / unit_divisor, 1)

    # Shares outstanding (weighted average diluted)
    # This is NOT in thousands typically
    shares_match = re.search(r'Diluted\s*([\d,]+)', content)
    if shares_match:
        shares = clean_number(shares_match.group(1))
        if shares:
            # Shares can be large numbers, convert to millions
            if shares > 1000000:
                shares = shares / 1000000
            elif shares > 1000:
                shares = shares / 1000
            data['SharesOutstanding'] = round(shares, 1)

    # === CASH FLOW METRICS ===

    ocf = extract_first_number(content, 'Net cash provided by operating activities')
    if ocf is None:
        ocf = extract_first_number(content, 'Net cash from operating activities')
    if ocf:
        data['OperatingCashFlow'] = round(ocf / unit_divisor, 1)

    capex = extract_first_number(content, 'Purchase of property and equipment')
    if capex is None:
        capex = extract_first_number(content, 'Purchases of property and equipment')
    if capex:
        data['CapitalExpenditures'] = round(capex / unit_divisor, 1)

    if data.get('OperatingCashFlow') and data.get('CapitalExpenditures'):
        data['FreeCashFlow'] = round(data['OperatingCashFlow'] - data['CapitalExpenditures'], 1)

    # === BLOCK-SPECIFIC REVENUE BREAKDOWN ===

    trans = extract_first_number(content, 'Transaction-based revenue')
    if trans:
        data['TransactionRevenue'] = round(trans / unit_divisor, 1)

    subs = extract_first_number(content, 'Subscription and services-based revenue')
    if subs:
        data['SubscriptionRevenue'] = round(subs / unit_divisor, 1)

    hw = extract_first_number(content, 'Hardware revenue')
    if hw:
        data['HardwareRevenue'] = round(hw / unit_divisor, 1)

    btc = extract_first_number(content, 'Bitcoin revenue')
    if btc:
        data['BitcoinRevenue'] = round(btc / unit_divisor, 1)

    # === KPIS (usually in billions or millions in text) ===

    # Square GPV - look for "GPV" or "gross payment volume" with billion/B
    gpv_match = re.search(r'(?:Square|Seller).{0,30}?(?:GPV|gross payment volume).{0,100}?\$([\d.]+)\s*(?:billion|B)', content, re.I)
    if gpv_match:
        gpv = clean_number(gpv_match.group(1))
        if gpv:
            data['SquareGPV'] = round(gpv * 1000, 0)  # billions to millions

    # Cash App inflows
    inflows_match = re.search(r'Cash App.{0,50}?inflows.{0,50}?\$([\d.]+)\s*(?:billion|B)', content, re.I)
    if inflows_match:
        inflows = clean_number(inflows_match.group(1))
        if inflows:
            data['CashAppInflows'] = round(inflows * 1000, 0)

    # Monthly actives
    ca_mau_match = re.search(r'Cash App.{0,100}?([\d.]+)\s*million.{0,30}?(?:monthly|active)', content, re.I)
    if ca_mau_match:
        ca_mau = clean_number(ca_mau_match.group(1))
        if ca_mau and ca_mau < 200:
            data['CashAppMonthlyActives'] = round(ca_mau, 1)

    sq_mau_match = re.search(r'([\d.]+)\s*million.{0,30}?(?:Square|seller).{0,30}?(?:monthly|active|merchant)', content, re.I)
    if sq_mau_match:
        sq_mau = clean_number(sq_mau_match.group(1))
        if sq_mau and sq_mau < 20:  # Square has ~4M merchants
            data['SquareMonthlyActives'] = round(sq_mau, 1)

    return data

def main():
    columns = [
        'Period', 'Revenue', 'GrossProfit', 'GrossMargin', 'OperatingIncome', 'OperatingMargin',
        'NetIncome', 'NetMargin', 'EPS', 'OperatingCashFlow', 'CapitalExpenditures', 'FreeCashFlow',
        'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding',
        'TransactionRevenue', 'SubscriptionRevenue', 'HardwareRevenue', 'BitcoinRevenue',
        'SquareGPV', 'CashAppInflows', 'CashAppMonthlyActives', 'SquareMonthlyActives'
    ]

    results = []
    for filepath in sorted(EXTRACTED_DIR.glob('*.txt')):
        try:
            data = parse_sec_filing(filepath)
            if data:
                rev_str = f"${data.get('Revenue', 0):,.1f}M" if data.get('Revenue') else "$?M"
                ni_str = f"${data.get('NetIncome', 0):,.1f}M" if data.get('NetIncome') is not None else "$?M"
                print(f"{filepath.name}: Rev={rev_str}, NI={ni_str}")
                results.append(data)
        except Exception as e:
            print(f"Error parsing {filepath.name}: {e}")

    # Sort chronologically
    def sort_key(d):
        p = d['Period']
        if p.startswith('FY'):
            return (int(p[2:]), 4)
        else:
            q, year = p.split()
            return (int(year), int(q[1]))

    results.sort(key=sort_key)

    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)

    print(f"\n{'='*60}")
    print(f"Wrote {len(results)} periods to {OUTPUT_FILE}")
    print(f"{'='*60}")

    print("\nRecent periods summary:")
    for r in results[-6:]:
        rev = f"${r.get('Revenue', 0):>10,.1f}M" if r.get('Revenue') else "         $?M"
        ni = f"${r.get('NetIncome', 0):>10,.1f}M" if r.get('NetIncome') is not None else "         $?M"
        gm = f"{r.get('GrossMargin', 0):>5.1f}%" if r.get('GrossMargin') else "   ?%"
        print(f"  {r['Period']:10} Rev:{rev}  NI:{ni}  GM:{gm}")

if __name__ == '__main__':
    main()
