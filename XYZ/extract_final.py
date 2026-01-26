#!/usr/bin/env python3
"""
Block, Inc. (XYZ) Financial Metrics Extraction - Final Version
Extracts from iXBRL HTML SEC filings using maximum values for annual totals.
"""
import re
import csv
from pathlib import Path

PDFS_DIR = Path("/Users/swilliams/Stocks/Research/XYZ/PDFs")
OUTPUT_FILE = Path("/Users/swilliams/Stocks/Research/XYZ/Reports/XYZ_Metrics.csv")
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

def clean_number(s):
    """Convert string to float."""
    if not s:
        return None
    s = str(s).replace('$', '').replace(',', '').replace(' ', '').strip()
    is_negative = '(' in s and ')' in s
    s = s.replace('(', '').replace(')', '').replace('-', '')
    try:
        val = float(s)
        return -val if is_negative else val
    except:
        return None

def extract_all_xbrl_values(content, concept_name):
    """Extract all values for a given XBRL concept."""
    # Pattern: name="xyz:Concept" or name="us-gaap:Concept" followed by >value<
    pattern = rf'{concept_name}[^>]*>([\d,\.\-]+)<'
    matches = re.findall(pattern, content, re.I)
    values = []
    for m in matches:
        val = clean_number(m)
        if val is not None and val != 0:
            values.append(val)
    return values

def get_max_value(values):
    """Return max absolute value, preserving sign."""
    if not values:
        return None
    return max(values, key=abs)

def get_first_value(values):
    """Return first value."""
    return values[0] if values else None

def parse_filing(filepath):
    """Parse SEC HTML/iXBRL filing."""
    filename = filepath.name

    # Get period from filename
    period_match = re.search(r'_(FY\d{4}|Q\d-\d{4})', filename)
    if not period_match:
        return None

    period_str = period_match.group(1)
    period = period_str if period_str.startswith('FY') else period_str.replace('-', ' ')
    is_annual = period.startswith('FY')

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    data = {'Period': period}

    # Values in SEC filings are in thousands
    unit_div = 1000  # Convert to millions

    # === REVENUE ===
    rev_values = extract_all_xbrl_values(content, 'us-gaap:Revenues')
    if rev_values:
        # For annual: take max (full year total)
        # For quarterly: also take max (the current quarter total is usually the largest complete value)
        data['Revenue'] = round(get_max_value(rev_values) / unit_div, 1)

    # === GROSS PROFIT ===
    gp_values = extract_all_xbrl_values(content, 'us-gaap:GrossProfit')
    if gp_values:
        data['GrossProfit'] = round(get_max_value(gp_values) / unit_div, 1)

    if data.get('Revenue') and data.get('GrossProfit'):
        data['GrossMargin'] = round(data['GrossProfit'] / data['Revenue'] * 100, 1)

    # === OPERATING INCOME/LOSS ===
    op_values = extract_all_xbrl_values(content, 'us-gaap:OperatingIncomeLoss')
    if op_values:
        # For operating income, get the value with largest absolute value
        data['OperatingIncome'] = round(get_max_value(op_values) / unit_div, 1)

    if data.get('Revenue') and data.get('OperatingIncome') is not None:
        data['OperatingMargin'] = round(data['OperatingIncome'] / data['Revenue'] * 100, 1)

    # === NET INCOME/LOSS ===
    ni_values = extract_all_xbrl_values(content, 'us-gaap:NetIncomeLoss')
    if ni_values:
        data['NetIncome'] = round(get_max_value(ni_values) / unit_div, 1)

    if data.get('Revenue') and data.get('NetIncome') is not None:
        data['NetMargin'] = round(data['NetIncome'] / data['Revenue'] * 100, 1)

    # === EPS (Diluted) - not in thousands ===
    eps_values = extract_all_xbrl_values(content, 'us-gaap:EarningsPerShareDiluted')
    if eps_values:
        # EPS values are small decimals, take the one with largest absolute value
        data['EPS'] = round(get_max_value(eps_values), 2)

    # === BALANCE SHEET ===
    # Stockholders' equity
    equity_values = extract_all_xbrl_values(content, 'us-gaap:StockholdersEquity')
    if equity_values:
        data['ShareholdersEquity'] = round(get_max_value(equity_values) / unit_div, 1)

    # Long-term debt
    debt_values = extract_all_xbrl_values(content, 'us-gaap:LongTermDebtNoncurrent')
    if not debt_values:
        debt_values = extract_all_xbrl_values(content, 'us-gaap:LongTermDebt')
    if debt_values:
        data['TotalDebt'] = round(get_max_value(debt_values) / unit_div, 1)

    # Cash
    cash_values = extract_all_xbrl_values(content, 'us-gaap:CashAndCashEquivalentsAtCarryingValue')
    if cash_values:
        data['CashAndEquivalents'] = round(get_max_value(cash_values) / unit_div, 1)

    # Shares outstanding (in thousands, convert to millions)
    shares_values = extract_all_xbrl_values(content, 'us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding')
    if shares_values:
        data['SharesOutstanding'] = round(get_max_value(shares_values) / 1000, 1)

    # === CASH FLOW ===
    ocf_values = extract_all_xbrl_values(content, 'us-gaap:NetCashProvidedByUsedInOperatingActivities')
    if ocf_values:
        data['OperatingCashFlow'] = round(get_max_value(ocf_values) / unit_div, 1)

    capex_values = extract_all_xbrl_values(content, 'us-gaap:PaymentsToAcquirePropertyPlantAndEquipment')
    if capex_values:
        data['CapitalExpenditures'] = round(abs(get_max_value(capex_values)) / unit_div, 1)

    if data.get('OperatingCashFlow') is not None and data.get('CapitalExpenditures'):
        data['FreeCashFlow'] = round(data['OperatingCashFlow'] - data['CapitalExpenditures'], 1)

    # === BLOCK-SPECIFIC REVENUE BREAKDOWN ===
    # Transaction-based
    trans_values = extract_all_xbrl_values(content, 'TransactionBasedRevenue')
    if not trans_values:
        trans_values = extract_all_xbrl_values(content, 'sq:TransactionBasedRevenue')
    if trans_values:
        data['TransactionRevenue'] = round(get_max_value(trans_values) / unit_div, 1)

    # Subscription
    subs_values = extract_all_xbrl_values(content, 'SubscriptionAndServicesBasedRevenue')
    if not subs_values:
        subs_values = extract_all_xbrl_values(content, 'sq:SubscriptionAndServicesBasedRevenue')
    if subs_values:
        data['SubscriptionRevenue'] = round(get_max_value(subs_values) / unit_div, 1)

    # Hardware
    hw_values = extract_all_xbrl_values(content, 'HardwareRevenue')
    if not hw_values:
        hw_values = extract_all_xbrl_values(content, 'sq:HardwareRevenue')
    if hw_values:
        data['HardwareRevenue'] = round(get_max_value(hw_values) / unit_div, 1)

    # Bitcoin
    btc_values = extract_all_xbrl_values(content, 'BitcoinRevenue')
    if not btc_values:
        btc_values = extract_all_xbrl_values(content, 'sq:BitcoinRevenue')
    if btc_values:
        data['BitcoinRevenue'] = round(get_max_value(btc_values) / unit_div, 1)

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
    for filepath in sorted(PDFS_DIR.glob('*.htm')):
        try:
            data = parse_filing(filepath)
            if data:
                rev_str = f"${data.get('Revenue', 0):,.1f}M" if data.get('Revenue') else "$?M"
                ni_str = f"${data.get('NetIncome', 0):,.1f}M" if data.get('NetIncome') is not None else "$?M"
                print(f"{data['Period']:10} {filepath.name}: Rev={rev_str:>15}, NI={ni_str:>12}")
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

    print(f"\n{'='*70}")
    print(f"Wrote {len(results)} periods to {OUTPUT_FILE}")
    print(f"{'='*70}")

    print("\nSummary of recent periods:")
    print(f"{'Period':10} {'Revenue':>12} {'GrossProfit':>12} {'GM%':>6} {'NetIncome':>12} {'EPS':>8}")
    print("-" * 70)
    for r in results[-8:]:
        rev = f"${r.get('Revenue', 0):,.0f}M" if r.get('Revenue') else "?"
        gp = f"${r.get('GrossProfit', 0):,.0f}M" if r.get('GrossProfit') else "?"
        gm = f"{r.get('GrossMargin', 0):.1f}%" if r.get('GrossMargin') else "?"
        ni = f"${r.get('NetIncome', 0):,.0f}M" if r.get('NetIncome') is not None else "?"
        eps = f"${r.get('EPS', 0):.2f}" if r.get('EPS') is not None else "?"
        print(f"{r['Period']:10} {rev:>12} {gp:>12} {gm:>6} {ni:>12} {eps:>8}")

if __name__ == '__main__':
    main()
