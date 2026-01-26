#!/usr/bin/env python3
import re, csv, sys
from pathlib import Path

EXTRACTED = Path("/Users/swilliams/Stocks/Research/XYZ/Extracted")
OUTPUT = Path("/Users/swilliams/Stocks/Research/XYZ/Reports/XYZ_Metrics.csv")
OUTPUT.parent.mkdir(exist_ok=True)

def clean_num(s):
    if not s: return None
    s = s.replace('$','').replace(',','').replace(' ','').strip()
    neg = '(' in s
    s = s.replace('(','').replace(')','')
    try:
        n = float(s)
        return -abs(n) if neg else n
    except: return None

def to_millions(val, unit='thousands'):
    if val is None: return None
    return round(val/1000, 1) if 'thousand' in unit.lower() else round(val, 1)

def find_nums_after(text, label, count=3):
    pattern = re.escape(label) + r'\s+([\d,\(\)\$\s]+)'
    m = re.search(pattern, text, re.I)
    if not m: return [None]*count
    nums_text = m.group(1)[:300]
    found = re.findall(r'[\(]?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*[\)]?', nums_text)
    result = [clean_num(n) for n in found[:count]]
    while len(result) < count: result.append(None)
    return result

def parse_file(path):
    print(f"Parsing {path.name}...")
    content = open(path, encoding='utf-8', errors='ignore').read()

    # Get period
    m = re.search(r'_(FY\d{4}|Q\d-\d{4})', path.name)
    if not m: return None
    period = m.group(1).replace('-',' ')

    # Detect units
    unit = 'thousands'
    if re.search(r'in millions', content[:100000], re.I): unit = 'millions'

    data = {'Period': period}

    # Find financial statements
    ops = re.search(r'CONSOLIDATED STATEMENTS OF OPERATIONS.{0,20000}', content, re.I | re.S)
    if ops:
        ops_txt = ops.group(0)

        # Revenue components
        for label, key in [
            ('Transaction-based revenue', 'TransactionRevenue'),
            ('Subscription and services', 'SubscriptionRevenue'),
            ('Hardware revenue', 'HardwareRevenue'),
            ('Bitcoin revenue', 'BitcoinRevenue'),
            ('Total net revenue', 'Revenue'),
            ('Gross profit', 'GrossProfit')
        ]:
            vals = find_nums_after(ops_txt, label, 3)
            if vals[0]: data[key] = to_millions(vals[0], unit)

        # Operating income/loss
        op_inc = find_nums_after(ops_txt, 'Operating income', 3)
        if op_inc[0]:
            data['OperatingIncome'] = to_millions(op_inc[0], unit)
        else:
            op_loss = find_nums_after(ops_txt, 'Operating loss', 3)
            if op_loss[0]: data['OperatingIncome'] = -to_millions(op_loss[0], unit)

        # Net income/loss
        net_inc = find_nums_after(ops_txt, 'Net income(?!.*per share)', 3)
        if net_inc[0]:
            data['NetIncome'] = to_millions(net_inc[0], unit)
        else:
            net_loss = find_nums_after(ops_txt, 'Net loss(?!.*per share)', 3)
            if net_loss[0]: data['NetIncome'] = -to_millions(net_loss[0], unit)

        # EPS
        eps_m = re.search(r'(?:Diluted|Net (?:income|loss) per share.*diluted)[^\d]+([\(\)]?\d+\.\d+[\(\)]?)', ops_txt, re.I)
        if eps_m: data['EPS'] = clean_num(eps_m.group(1))

        # Margins
        if data.get('Revenue'):
            if data.get('GrossProfit'):
                data['GrossMargin'] = round(100 * data['GrossProfit'] / data['Revenue'], 1)
            if data.get('OperatingIncome'):
                data['OperatingMargin'] = round(100 * data['OperatingIncome'] / data['Revenue'], 1)
            if data.get('NetIncome'):
                data['NetMargin'] = round(100 * data['NetIncome'] / data['Revenue'], 1)

    # Balance sheet
    bs = re.search(r'CONSOLIDATED BALANCE SHEETS.{0,20000}', content, re.I | re.S)
    if bs:
        bs_txt = bs.group(0)
        cash_v = find_nums_after(bs_txt, 'Cash and cash equivalents', 2)
        inv_v = find_nums_after(bs_txt, 'Short-term investments', 2)
        cash = to_millions(cash_v[0], unit) if cash_v[0] else 0
        inv = to_millions(inv_v[0], unit) if inv_v[0] else 0
        if cash or inv: data['CashAndEquivalents'] = cash + inv

        ltd_v = find_nums_after(bs_txt, 'Long-term debt', 2)
        std_v = find_nums_after(bs_txt, 'Short-term debt', 2)
        ltd = to_millions(ltd_v[0], unit) if ltd_v[0] else 0
        std = to_millions(std_v[0], unit) if std_v[0] else 0
        if ltd or std: data['TotalDebt'] = ltd + std

        eq_v = find_nums_after(bs_txt, 'Total stockholders', 2)
        if eq_v[0]: data['ShareholdersEquity'] = to_millions(eq_v[0], unit)

        sh_m = re.search(r'(?:Shares outstanding|Common stock.*outstanding)[^\d]+(\d+(?:\.\d+)?)', bs_txt, re.I)
        if sh_m: data['SharesOutstanding'] = clean_num(sh_m.group(1))

    # Cash flow
    cf = re.search(r'CONSOLIDATED STATEMENTS OF CASH FLOWS.{0,15000}', content, re.I | re.S)
    if cf:
        cf_txt = cf.group(0)
        ocf_m = re.search(r'Net cash (?:provided by|used in) operating activities[^\d]+([\(\)]?\d{1,3}(?:,\d{3})*)', cf_txt, re.I)
        if ocf_m: data['OperatingCashFlow'] = to_millions(clean_num(ocf_m.group(1)), unit)

        capex_m = re.search(r'Purchase.*property and equipment[^\d]+([\(\)]?\d{1,3}(?:,\d{3})*)', cf_txt, re.I)
        if capex_m:
            capex = to_millions(clean_num(capex_m.group(1)), unit)
            data['CapitalExpenditures'] = abs(capex) if capex else None
            if data.get('OperatingCashFlow') and data.get('CapitalExpenditures'):
                data['FreeCashFlow'] = data['OperatingCashFlow'] - data['CapitalExpenditures']

    # KPIs
    gpv_m = re.search(r'(?:Square|Seller).*?(?:GPV|gross payment volume).*?(\d{1,3}(?:\.\d+)?)\s*billion', content, re.I)
    if gpv_m: data['SquareGPV'] = float(gpv_m.group(1)) * 1000

    ca_inf_m = re.search(r'Cash App.*?(?:inflow|volume).*?(\d{1,3}(?:\.\d+)?)\s*billion', content, re.I)
    if ca_inf_m: data['CashAppInflows'] = float(ca_inf_m.group(1)) * 1000

    ca_act_m = re.search(r'Cash App.*?monthly.*?active.*?(\d{1,3}(?:\.\d+)?)\s*million', content, re.I)
    if ca_act_m: data['CashAppMonthlyActives'] = float(ca_act_m.group(1))

    sq_act_m = re.search(r'Square.*?(?:seller|monthly).*?active.*?(\d{1,3}(?:\.\d+)?)\s*million', content, re.I)
    if sq_act_m: data['SquareMonthlyActives'] = float(sq_act_m.group(1))

    print(f"  → Revenue: ${data.get('Revenue','?')}M, Net Income: ${data.get('NetIncome','?')}M")
    return data

def sort_key(d):
    p = d['Period']
    if p.startswith('FY'):
        return (int(p[2:]), 5)
    q, y = p.split()
    return (int(y), int(q[1]))

# Main
all_data = []
for f in sorted(EXTRACTED.glob("XYZ_*.txt")):
    try:
        d = parse_file(f)
        if d: all_data.append(d)
    except Exception as e:
        print(f"ERROR {f.name}: {e}")

all_data.sort(key=sort_key)

cols = ['Period','Revenue','GrossProfit','GrossMargin','OperatingIncome','OperatingMargin',
        'NetIncome','NetMargin','EPS','OperatingCashFlow','CapitalExpenditures','FreeCashFlow',
        'ShareholdersEquity','TotalDebt','CashAndEquivalents','SharesOutstanding',
        'TransactionRevenue','SubscriptionRevenue','HardwareRevenue','BitcoinRevenue',
        'SquareGPV','CashAppInflows','CashAppMonthlyActives','SquareMonthlyActives']

with open(OUTPUT, 'w', newline='') as f:
    w = csv.DictWriter(f, cols)
    w.writeheader()
    for d in all_data:
        w.writerow({c: d.get(c,'') for c in cols})

print(f"\n✓ Wrote {len(all_data)} periods to {OUTPUT}")
if all_data:
    print("\nMost recent:")
    for d in all_data[-3:]:
        print(f"  {d['Period']:10} Rev=${d.get('Revenue','?'):>7}M  NI=${d.get('NetIncome','?'):>7}M")
