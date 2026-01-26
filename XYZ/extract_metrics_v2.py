#!/usr/bin/env python3
"""
Block, Inc. (XYZ) Financial Metrics Extraction Script
Parses SEC filings (10-K and 10-Q) to extract financial data.
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

def extract_number_after(text, label, position=0):
    """Extract the nth number following a label."""
    # Pattern: label followed by optional whitespace/$ and a number
    pattern = re.escape(label) + r'[^\d\(]*(\$?[\(\d,\.\)]+)'
    matches = re.findall(pattern, text, re.I)
    if matches and len(matches) > position:
        return clean_number(matches[position])
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
        # Convert Q1-2024 to Q1 2024
        period = period_str.replace('-', ' ')  # e.g., Q1 2024

    is_annual = 'FY' in period

    # Read file content
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Initialize data dict
    data = {'Period': period}

    # Detect if values are in thousands (default) or millions
    # SEC filings typically state "(In thousands, except per share data)"
    unit_divisor = 1000  # Convert thousands to millions
    if re.search(r'in millions', content[:50000], re.I) and not re.search(r'in thousands', content[:50000], re.I):
        unit_divisor = 1  # Already in millions

    # === INCOME STATEMENT METRICS ===

    # Total net revenue - look for pattern like "Total net revenue $24,121,053$21,915,623"
    # The first number is current year, second is prior year
    rev_match = re.search(r'Total net revenue\s*\$?([\d,]+)\s*\$?([\d,]+)?', content)
    if rev_match:
        rev_val = clean_number(rev_match.group(1))
        if rev_val:
            data['Revenue'] = round(rev_val / unit_divisor, 1)

    # Gross profit
    gp_match = re.search(r'Gross profit\s*\$?([\d,]+)', content)
    if gp_match:
        gp_val = clean_number(gp_match.group(1))
        if gp_val:
            data['GrossProfit'] = round(gp_val / unit_divisor, 1)

    # Calculate gross margin
    if data.get('Revenue') and data.get('GrossProfit'):
        data['GrossMargin'] = round(data['GrossProfit'] / data['Revenue'] * 100, 1)

    # Operating income/loss
    # Pattern handles both "Operating income" and "Operating loss"
    op_match = re.search(r'(?:Operating income|Operating loss|Loss from operations)\s*\(?\$?([\d,]+)\)?', content)
    if op_match:
        op_val = clean_number(op_match.group(1))
        if op_val:
            # Check if it's a loss (negative)
            if re.search(r'(?:Operating loss|Loss from operations)', content[:op_match.end()]):
                op_val = -abs(op_val)
            data['OperatingIncome'] = round(op_val / unit_divisor, 1)

    # Calculate operating margin
    if data.get('Revenue') and data.get('OperatingIncome') is not None:
        data['OperatingMargin'] = round(data['OperatingIncome'] / data['Revenue'] * 100, 1)

    # Net income - look for net income or net loss
    ni_patterns = [
        r'Net income\s*\$?([\d,]+)',
        r'Net loss\s*\(?\$?([\d,]+)\)?',
        r'Net income \(loss\)\s*\(?\$?([\d,]+)\)?'
    ]
    for pattern in ni_patterns:
        ni_match = re.search(pattern, content)
        if ni_match:
            ni_val = clean_number(ni_match.group(1))
            if ni_val:
                # Check if it's labeled as a loss
                if 'loss' in pattern.lower() or (ni_match.start() > 0 and '(' in content[ni_match.start()-1:ni_match.start()+5]):
                    ni_val = -abs(ni_val)
                data['NetIncome'] = round(ni_val / unit_divisor, 1)
            break

    # Calculate net margin
    if data.get('Revenue') and data.get('NetIncome') is not None:
        data['NetMargin'] = round(data['NetIncome'] / data['Revenue'] * 100, 1)

    # EPS (Diluted) - typically in per share format already (not thousands)
    eps_match = re.search(r'(?:Diluted|Net income per share.{0,20}diluted)\s*\$?\(?([\d,\.]+)\)?', content)
    if eps_match:
        eps_val = clean_number(eps_match.group(1))
        if eps_val:
            # EPS is not in thousands
            data['EPS'] = round(eps_val, 2)

    # === BALANCE SHEET METRICS ===

    # Total stockholders' equity
    equity_match = re.search(r"(?:Total stockholders'? equity|Stockholders'? equity)\s*\$?([\d,]+)", content)
    if equity_match:
        eq_val = clean_number(equity_match.group(1))
        if eq_val:
            data['ShareholdersEquity'] = round(eq_val / unit_divisor, 1)

    # Total debt (short-term + long-term)
    # Look for long-term debt first
    debt_match = re.search(r'(?:Long-term debt|Total debt|Notes payable)\s*\$?([\d,]+)', content)
    if debt_match:
        debt_val = clean_number(debt_match.group(1))
        if debt_val:
            data['TotalDebt'] = round(debt_val / unit_divisor, 1)

    # Cash and cash equivalents
    cash_match = re.search(r'Cash and cash equivalents\s*\$?([\d,]+)', content)
    if cash_match:
        cash_val = clean_number(cash_match.group(1))
        if cash_val:
            data['CashAndEquivalents'] = round(cash_val / unit_divisor, 1)

    # Shares outstanding (diluted) - in millions, not thousands
    shares_match = re.search(r'(?:Diluted|Weighted.average.{0,30}diluted)\s*([\d,\.]+)', content)
    if shares_match:
        shares_val = clean_number(shares_match.group(1))
        if shares_val:
            # Shares are typically reported in millions or actual count
            # If the number is very large, it's in actual shares
            if shares_val > 1000:
                shares_val = shares_val / 1000  # Convert to millions
            data['SharesOutstanding'] = round(shares_val, 1)

    # === CASH FLOW METRICS ===

    # Operating cash flow
    ocf_match = re.search(r'Net cash (?:provided by|from) operating activities\s*\$?([\d,]+)', content)
    if ocf_match:
        ocf_val = clean_number(ocf_match.group(1))
        if ocf_val:
            data['OperatingCashFlow'] = round(ocf_val / unit_divisor, 1)

    # Capital expenditures
    capex_match = re.search(r'(?:Purchase of|Purchases of) property and equipment\s*\(?\$?([\d,]+)\)?', content)
    if capex_match:
        capex_val = clean_number(capex_match.group(1))
        if capex_val:
            data['CapitalExpenditures'] = round(capex_val / unit_divisor, 1)

    # Calculate Free Cash Flow
    if data.get('OperatingCashFlow') and data.get('CapitalExpenditures'):
        data['FreeCashFlow'] = round(data['OperatingCashFlow'] - data['CapitalExpenditures'], 1)

    # === BLOCK-SPECIFIC KPIS ===

    # Transaction-based revenue
    trans_match = re.search(r'Transaction-based revenue\s*\$?([\d,]+)', content)
    if trans_match:
        trans_val = clean_number(trans_match.group(1))
        if trans_val:
            data['TransactionRevenue'] = round(trans_val / unit_divisor, 1)

    # Subscription and services revenue
    subs_match = re.search(r'Subscription and services-based revenue\s*\$?([\d,]+)', content)
    if subs_match:
        subs_val = clean_number(subs_match.group(1))
        if subs_val:
            data['SubscriptionRevenue'] = round(subs_val / unit_divisor, 1)

    # Hardware revenue
    hw_match = re.search(r'Hardware revenue\s*\$?([\d,]+)', content)
    if hw_match:
        hw_val = clean_number(hw_match.group(1))
        if hw_val:
            data['HardwareRevenue'] = round(hw_val / unit_divisor, 1)

    # Bitcoin revenue
    btc_match = re.search(r'Bitcoin revenue\s*\$?([\d,]+)', content)
    if btc_match:
        btc_val = clean_number(btc_match.group(1))
        if btc_val:
            data['BitcoinRevenue'] = round(btc_val / unit_divisor, 1)

    # Square GPV (Gross Payment Volume) - usually in billions
    gpv_match = re.search(r'(?:Square|Seller)\s*(?:GPV|gross payment volume).{0,50}?\$([\d\.]+)\s*(?:billion|B)', content, re.I)
    if gpv_match:
        gpv_val = clean_number(gpv_match.group(1))
        if gpv_val:
            data['SquareGPV'] = round(gpv_val * 1000, 0)  # Convert billions to millions

    # Cash App inflows - usually in billions
    inflows_match = re.search(r'Cash App.{0,50}?inflows.{0,30}?\$([\d\.]+)\s*(?:billion|B)', content, re.I)
    if inflows_match:
        inflows_val = clean_number(inflows_match.group(1))
        if inflows_val:
            data['CashAppInflows'] = round(inflows_val * 1000, 0)  # Convert billions to millions

    # Monthly active users - Cash App
    ca_mau_match = re.search(r'Cash App.{0,100}?([\d\.]+)\s*million.{0,20}?monthly.{0,20}?active', content, re.I)
    if ca_mau_match:
        ca_mau_val = clean_number(ca_mau_match.group(1))
        if ca_mau_val:
            data['CashAppMonthlyActives'] = round(ca_mau_val, 1)

    # Monthly active users - Square
    sq_mau_match = re.search(r'Square.{0,100}?([\d\.]+)\s*million.{0,20}?(?:monthly.{0,20}?active|merchants)', content, re.I)
    if sq_mau_match:
        sq_mau_val = clean_number(sq_mau_match.group(1))
        if sq_mau_val and sq_mau_val < 50:  # Square has ~4M merchants
            data['SquareMonthlyActives'] = round(sq_mau_val, 1)

    return data

def main():
    # Define CSV columns
    columns = [
        'Period', 'Revenue', 'GrossProfit', 'GrossMargin', 'OperatingIncome', 'OperatingMargin',
        'NetIncome', 'NetMargin', 'EPS', 'OperatingCashFlow', 'CapitalExpenditures', 'FreeCashFlow',
        'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding',
        'TransactionRevenue', 'SubscriptionRevenue', 'HardwareRevenue', 'BitcoinRevenue',
        'SquareGPV', 'CashAppInflows', 'CashAppMonthlyActives', 'SquareMonthlyActives'
    ]

    # Parse all files
    results = []
    for filepath in sorted(EXTRACTED_DIR.glob('*.txt')):
        try:
            data = parse_sec_filing(filepath)
            if data:
                print(f"Parsed {filepath.name}: Rev=${data.get('Revenue', '?')}M, NI=${data.get('NetIncome', '?')}M")
                results.append(data)
        except Exception as e:
            print(f"Error parsing {filepath.name}: {e}")

    # Sort by period (chronologically)
    def sort_key(d):
        p = d['Period']
        if p.startswith('FY'):
            year = int(p[2:])
            return (year, 4)  # FY comes after Q4 of same year
        else:
            q, year = p.split()
            q_num = int(q[1])
            return (int(year), q_num)

    results.sort(key=sort_key)

    # Write to CSV
    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} periods to {OUTPUT_FILE}")

    # Print summary of recent periods
    print("\nMost recent periods:")
    for r in results[-5:]:
        rev = f"${r.get('Revenue', '?'):,.1f}M" if r.get('Revenue') else "$?M"
        ni = f"${r.get('NetIncome', '?'):,.1f}M" if r.get('NetIncome') is not None else "$?M"
        print(f"  {r['Period']:10} Revenue: {rev:>15}  Net Income: {ni:>15}")

if __name__ == '__main__':
    main()
