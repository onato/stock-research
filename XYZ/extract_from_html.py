#!/usr/bin/env python3
"""
Block, Inc. (XYZ) Financial Metrics Extraction - Parse from original HTML/iXBRL
"""
import re
import csv
from pathlib import Path
from html.parser import HTMLParser

PDFS_DIR = Path("/Users/swilliams/Stocks/Research/XYZ/PDFs")
OUTPUT_FILE = Path("/Users/swilliams/Stocks/Research/XYZ/Reports/XYZ_Metrics.csv")
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

def clean_number(s):
    """Convert string to float."""
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

def extract_xbrl_values(content, concept_name):
    """Extract all values for a given XBRL concept from iXBRL HTML."""
    # Pattern for iXBRL tags like: <ix:nonfraction name="us-gaap:Revenues">1,234,567</ix:nonfraction>
    # or just: name="us-gaap:Revenues"...>1,234,567<
    pattern = rf'{concept_name}[^>]*>([^<]+)<'
    matches = re.findall(pattern, content, re.I)
    return [clean_number(m) for m in matches if clean_number(m)]

def extract_value_near_label(content, label, max_chars=500):
    """Extract first numeric value near a text label."""
    # Find label position
    pattern = re.escape(label) + r'.{0,' + str(max_chars) + r'}'
    match = re.search(pattern, content, re.I | re.S)
    if match:
        text = match.group()
        # Find first number (with commas)
        num_match = re.search(r'>(\(?[\d,]+\)?)<', text)
        if num_match:
            return clean_number(num_match.group(1))
    return None

def parse_html_filing(filepath):
    """Parse SEC HTML/iXBRL filing."""
    filename = filepath.name

    # Get period from filename
    period_match = re.search(r'_(FY\d{4}|Q\d-\d{4})', filename)
    if not period_match:
        return None

    period_str = period_match.group(1)
    period = period_str if period_str.startswith('FY') else period_str.replace('-', ' ')

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    data = {'Period': period}

    # Detect if 10-K (annual) or 10-Q (quarterly)
    is_annual = '10-K' in filename or 'FY' in period

    # Unit divisor (SEC filings in thousands)
    unit_div = 1000  # Convert to millions

    # === REVENUE ===
    # Look for us-gaap:Revenues or RevenueFromContractWithCustomerExcludingAssessedTax
    rev_values = extract_xbrl_values(content, 'us-gaap:Revenues')
    if not rev_values:
        rev_values = extract_xbrl_values(content, 'RevenueFromContractWithCustomerExcludingAssessedTax')
    if not rev_values:
        rev_values = extract_xbrl_values(content, 'xyz:TotalNetRevenue')

    # For annual reports, sum quarterly values or take the largest
    if rev_values:
        # If annual, the largest value is typically the annual total
        if is_annual:
            data['Revenue'] = round(max(rev_values) / unit_div, 1)
        else:
            # For quarterly, take the first (most recent quarter)
            data['Revenue'] = round(rev_values[0] / unit_div, 1)

    # === GROSS PROFIT ===
    gp_values = extract_xbrl_values(content, 'us-gaap:GrossProfit')
    if not gp_values:
        gp_values = extract_xbrl_values(content, 'GrossProfit')
    if gp_values:
        if is_annual:
            data['GrossProfit'] = round(max(gp_values) / unit_div, 1)
        else:
            data['GrossProfit'] = round(gp_values[0] / unit_div, 1)

    # Gross margin
    if data.get('Revenue') and data.get('GrossProfit'):
        data['GrossMargin'] = round(data['GrossProfit'] / data['Revenue'] * 100, 1)

    # === OPERATING INCOME/LOSS ===
    op_values = extract_xbrl_values(content, 'us-gaap:OperatingIncomeLoss')
    if not op_values:
        op_values = extract_xbrl_values(content, 'OperatingIncomeLoss')
    if op_values:
        # For operating income, use first value (current period)
        data['OperatingIncome'] = round(op_values[0] / unit_div, 1)

    if data.get('Revenue') and data.get('OperatingIncome') is not None:
        data['OperatingMargin'] = round(data['OperatingIncome'] / data['Revenue'] * 100, 1)

    # === NET INCOME/LOSS ===
    ni_values = extract_xbrl_values(content, 'us-gaap:NetIncomeLoss')
    if not ni_values:
        ni_values = extract_xbrl_values(content, 'NetIncomeLoss')
    if ni_values:
        data['NetIncome'] = round(ni_values[0] / unit_div, 1)

    if data.get('Revenue') and data.get('NetIncome') is not None:
        data['NetMargin'] = round(data['NetIncome'] / data['Revenue'] * 100, 1)

    # === EPS (Diluted) ===
    eps_values = extract_xbrl_values(content, 'us-gaap:EarningsPerShareDiluted')
    if not eps_values:
        eps_values = extract_xbrl_values(content, 'EarningsPerShareDiluted')
    if eps_values:
        data['EPS'] = round(eps_values[0], 2)  # EPS is per share, not thousands

    # === BALANCE SHEET ===
    # Stockholders' equity
    equity_values = extract_xbrl_values(content, 'us-gaap:StockholdersEquity')
    if not equity_values:
        equity_values = extract_xbrl_values(content, 'StockholdersEquity')
    if equity_values:
        data['ShareholdersEquity'] = round(max(equity_values) / unit_div, 1)

    # Long-term debt
    debt_values = extract_xbrl_values(content, 'us-gaap:LongTermDebt')
    if not debt_values:
        debt_values = extract_xbrl_values(content, 'LongTermDebtNoncurrent')
    if debt_values:
        data['TotalDebt'] = round(max(debt_values) / unit_div, 1)

    # Cash and equivalents
    cash_values = extract_xbrl_values(content, 'us-gaap:CashAndCashEquivalentsAtCarryingValue')
    if not cash_values:
        cash_values = extract_xbrl_values(content, 'CashAndCashEquivalentsAtCarryingValue')
    if cash_values:
        data['CashAndEquivalents'] = round(max(cash_values) / unit_div, 1)

    # Shares outstanding (diluted weighted average)
    shares_values = extract_xbrl_values(content, 'us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding')
    if not shares_values:
        shares_values = extract_xbrl_values(content, 'WeightedAverageNumberOfDilutedSharesOutstanding')
    if shares_values:
        # Shares are in thousands, convert to millions
        data['SharesOutstanding'] = round(shares_values[0] / 1000, 1)

    # === CASH FLOW ===
    ocf_values = extract_xbrl_values(content, 'us-gaap:NetCashProvidedByUsedInOperatingActivities')
    if not ocf_values:
        ocf_values = extract_xbrl_values(content, 'NetCashProvidedByUsedInOperatingActivities')
    if ocf_values:
        data['OperatingCashFlow'] = round(ocf_values[0] / unit_div, 1)

    capex_values = extract_xbrl_values(content, 'us-gaap:PaymentsToAcquirePropertyPlantAndEquipment')
    if not capex_values:
        capex_values = extract_xbrl_values(content, 'PaymentsToAcquirePropertyPlantAndEquipment')
    if capex_values:
        data['CapitalExpenditures'] = round(abs(capex_values[0]) / unit_div, 1)

    if data.get('OperatingCashFlow') is not None and data.get('CapitalExpenditures'):
        data['FreeCashFlow'] = round(data['OperatingCashFlow'] - data['CapitalExpenditures'], 1)

    # === BLOCK-SPECIFIC METRICS ===
    # These may have custom XBRL names

    # Transaction-based revenue
    trans_values = extract_xbrl_values(content, 'TransactionBasedRevenue')
    if trans_values:
        data['TransactionRevenue'] = round(trans_values[0] / unit_div, 1)

    # Subscription and services revenue
    subs_values = extract_xbrl_values(content, 'SubscriptionAndServicesBasedRevenue')
    if subs_values:
        data['SubscriptionRevenue'] = round(subs_values[0] / unit_div, 1)

    # Hardware revenue
    hw_values = extract_xbrl_values(content, 'HardwareRevenue')
    if hw_values:
        data['HardwareRevenue'] = round(hw_values[0] / unit_div, 1)

    # Bitcoin revenue
    btc_values = extract_xbrl_values(content, 'BitcoinRevenue')
    if btc_values:
        data['BitcoinRevenue'] = round(btc_values[0] / unit_div, 1)

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
            data = parse_html_filing(filepath)
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

    print("\nRecent periods:")
    for r in results[-6:]:
        rev = f"${r.get('Revenue', 0):>10,.1f}M" if r.get('Revenue') else "         $?M"
        ni = f"${r.get('NetIncome', 0):>10,.1f}M" if r.get('NetIncome') is not None else "         $?M"
        gm = f"{r.get('GrossMargin', 0):>5.1f}%" if r.get('GrossMargin') else "   ?%"
        print(f"  {r['Period']:10} Rev:{rev}  NI:{ni}  GM:{gm}")

if __name__ == '__main__':
    main()
