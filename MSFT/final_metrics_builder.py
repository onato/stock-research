#!/usr/bin/env python3
"""
Final MSFT metrics CSV builder - extracts quarterly and annual data
"""
import re
import csv

BASE = "/Users/swilliams/Stocks/Research/MSFT/Extracted"

def clean_number(s):
    """Remove commas and convert to int"""
    return int(s.replace(',', ''))

def clean_float(s):
    """Convert to float"""
    return float(s)

def extract_10q(filepath):
    """Extract Q data from 10-Q - using the 'Three Months Ended' column (first period)"""
    with open(filepath, 'r') as f:
        text = f.read()

    data = {}

    # Get segment data from SEGMENT RESULTS section
    # Pattern: Productivity... Revenue $ XX,XXX $ (prior) ... Intelligent Cloud Revenue $ XX,XXX
    seg_pat = (
        r'SEGMENT RESULTS OF OPERATIONS.*?'
        r'Productivity and Business Processes\s*Revenue\s*\$\s*([\d,]+)\s*\$.*?'
        r'Intelligent Cloud\s*Revenue\s*\$\s*([\d,]+)\s*\$.*?'
        r'More Personal Computing\s*Revenue\s*\$\s*([\d,]+)\s*\$'
    )
    seg_match = re.search(seg_pat, text, re.DOTALL)
    if seg_match:
        data['ProductivityRevenue'] = clean_number(seg_match.group(1))
        data['CloudRevenue'] = clean_number(seg_match.group(2))
        data['PersonalComputingRevenue'] = clean_number(seg_match.group(3))

    # Get income statement data - find the section with "Three Months Ended"
    # Pattern is: "Total revenue\n33,055\n29,084" - we want the first number (current quarter)
    inc_pat = r'INCOME STATEMENTS.*?Three Months Ended.*?\n(.*?)\nRefer to accompanying notes'
    inc_match = re.search(inc_pat, text, re.DOTALL)

    if inc_match:
        inc_section = inc_match.group(1)

        # Total revenue: "Total revenue\n77,673\n65,585"
        rev_match = re.search(r'Total revenue\s*\n\s*([\d,]+)', inc_section)
        if rev_match:
            data['Revenue'] = clean_number(rev_match.group(1))

        # Gross margin
        gm_match = re.search(r'Gross margin\s*\n\s*([\d,]+)', inc_section)
        if gm_match:
            data['GrossProfit'] = clean_number(gm_match.group(1))

        # Operating income
        oi_match = re.search(r'Operating income\s*\n\s*([\d,]+)', inc_section)
        if oi_match:
            data['OperatingIncome'] = clean_number(oi_match.group(1))

        # Net income: "Net income\n$\n27,747\n$\n24,667"
        ni_match = re.search(r'Net income\s*\n\s*\$\s*\n\s*([\d,]+)', inc_section)
        if ni_match:
            data['NetIncome'] = clean_number(ni_match.group(1))

        # EPS Diluted: "Diluted\n$\n3.72\n$\n3.30"
        eps_match = re.search(r'Diluted\s*\n\s*\$\s*\n\s*([\d.]+)\s*\n\s*\$', inc_section)
        if eps_match:
            data['EPS'] = clean_float(eps_match.group(1))

    return data

def extract_10k(filepath):
    """Extract annual data from 10-K"""
    with open(filepath, 'r') as f:
        text = f.read()

    data = {}

    # Segment data
    seg_pat = (
        r'SEGMENT RESULTS OF OPERATIONS.*?'
        r'Productivity and Business Processes\s*Revenue\s*\$\s*([\d,]+)\s*\$.*?'
        r'Intelligent Cloud\s*Revenue\s*\$\s*([\d,]+)\s*\$.*?'
        r'More Personal Computing\s*Revenue\s*\$\s*([\d,]+)\s*\$'
    )
    seg_match = re.search(seg_pat, text, re.DOTALL)
    if seg_match:
        data['ProductivityRevenue'] = clean_number(seg_match.group(1))
        data['CloudRevenue'] = clean_number(seg_match.group(2))
        data['PersonalComputingRevenue'] = clean_number(seg_match.group(3))

    # Income statement
    inc_pat = r'CONSOLIDATED INCOME STATEMENTS.*?Year Ended June 30.*?\n(.*?)See accompanying notes'
    inc_match = re.search(inc_pat, text, re.DOTALL)

    if inc_match:
        inc_section = inc_match.group(1)

        rev_match = re.search(r'Total revenue\s*\n\s*([\d,]+)', inc_section)
        if rev_match:
            data['Revenue'] = clean_number(rev_match.group(1))

        gm_match = re.search(r'Gross margin\s*\n\s*([\d,]+)', inc_section)
        if gm_match:
            data['GrossProfit'] = clean_number(gm_match.group(1))

        oi_match = re.search(r'Operating income\s*\n\s*([\d,]+)', inc_section)
        if oi_match:
            data['OperatingIncome'] = clean_number(oi_match.group(1))

        ni_match = re.search(r'Net income\s*\n\s*\$\s*\n\s*([\d,]+)', inc_section)
        if ni_match:
            data['NetIncome'] = clean_number(ni_match.group(1))

        eps_match = re.search(r'Diluted\s*\n\s*\$\s*\n\s*([\d.]+)\s*\n', inc_section)
        if eps_match:
            data['EPS'] = clean_float(eps_match.group(1))

    # Cash flow
    cf_pat = r'CONSOLIDATED CASH FLOWS STATEMENTS.*?Year Ended June 30.*?\n(.*?)CONSOLIDATED BALANCE SHEETS'
    cf_match = re.search(cf_pat, text, re.DOTALL)

    if cf_match:
        cf_section = cf_match.group(1)

        ocf_match = re.search(r'Net cash from operations\s*\n\s*\$?\s*([\d,]+)', cf_section)
        if ocf_match:
            data['OperatingCashFlow'] = clean_number(ocf_match.group(1))

        capex_match = re.search(r'Additions to property and equipment\s*\n\s*\(\s*([\d,]+)', cf_section)
        if capex_match:
            data['CapEx'] = clean_number(capex_match.group(1))

    if 'OperatingCashFlow' in data and 'CapEx' in data:
        data['FreeCashFlow'] = data['OperatingCashFlow'] - data['CapEx']

    # Balance sheet
    bs_pat = r'CONSOLIDATED BALANCE SHEETS.*?June 30.*?\n(.*?)CONSOLIDATED CASH FLOWS'
    bs_match = re.search(bs_pat, text, re.DOTALL)

    if bs_match:
        bs_section = bs_match.group(1)

        eq_match = re.search(r'Total stockholders\' equity\s*\n\s*\$?\s*([\d,]+)', bs_section)
        if eq_match:
            data['ShareholdersEquity'] = clean_number(eq_match.group(1))

        cash_match = re.search(r'Cash and cash equivalents\s*\n\s*\$?\s*([\d,]+)', bs_section)
        if cash_match:
            data['CashAndEquivalents'] = clean_number(cash_match.group(1))

        # Total debt = current + long-term
        cd_match = re.search(r'Current portion of long-term debt\s*\n\s*\$?\s*([\d,]+)', bs_section)
        ltd_match = re.search(r'Long-term debt\s*\n\s*\$?\s*([\d,]+)', bs_section)

        total_debt = 0
        if cd_match:
            total_debt += clean_number(cd_match.group(1))
        if ltd_match:
            total_debt += clean_number(ltd_match.group(1))
        if total_debt > 0:
            data['TotalDebt'] = total_debt

    return data

def calc_margins(data):
    """Calculate margin percentages"""
    if 'Revenue' in data and 'GrossProfit' in data:
        data['GrossMargin'] = round(data['GrossProfit'] / data['Revenue'] * 100, 1)
    if 'Revenue' in data and 'OperatingIncome' in data:
        data['OperatingMargin'] = round(data['OperatingIncome'] / data['Revenue'] * 100, 1)
    if 'Revenue' in data and 'NetIncome' in data:
        data['NetMargin'] = round(data['NetIncome'] / data['Revenue'] * 100, 1)
    return data

# Read existing CSV to preserve some data (like shares outstanding that may be missing from 10-Ks)
existing_data = {}
with open('/Users/swilliams/Stocks/Research/MSFT/Reports/MSFT_Metrics.csv', 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        existing_data[row['Period']] = row

# Build complete dataset
all_periods = {}

# Annual data FY2015-FY2025
for year in range(2015, 2026):
    fy = f"FY{year}"
    filepath = f"{BASE}/MSFT_10K_{fy}.txt"

    print(f"Processing {fy}...")
    data = extract_10k(filepath)
    data = calc_margins(data)

    # Preserve SharesOutstanding from existing if available
    if fy in existing_data and existing_data[fy].get('SharesOutstanding'):
        data['SharesOutstanding'] = existing_data[fy]['SharesOutstanding']

    all_periods[fy] = data

# Quarterly data Q1-Q3 for FY2020-FY2025, Q1-Q2 for FY2026
quarters = []
for year in range(2020, 2026):
    for q in ['Q1', 'Q2', 'Q3']:
        quarters.append((q, year))

quarters.append(('Q1', 2026))
quarters.append(('Q2', 2026))

for q, year in quarters:
    period = f"{q} FY{year}"
    filepath = f"{BASE}/MSFT_10Q_{q}-FY{year}.txt"

    print(f"Processing {period}...")
    try:
        data = extract_10q(filepath)
        data = calc_margins(data)

        # Preserve SharesOutstanding from existing if available
        if period in existing_data and existing_data[period].get('SharesOutstanding'):
            data['SharesOutstanding'] = existing_data[period]['SharesOutstanding']

        all_periods[period] = data
    except FileNotFoundError:
        print(f"  WARNING: File not found")
    except Exception as e:
        print(f"  ERROR: {e}")

# Write CSV in chronological order
columns = [
    'Period', 'Revenue', 'GrossProfit', 'GrossMargin', 'OperatingIncome', 'OperatingMargin',
    'NetIncome', 'NetMargin', 'EPS', 'FreeCashFlow', 'OperatingCashFlow', 'CapEx',
    'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding',
    'CloudRevenue', 'ProductivityRevenue', 'PersonalComputingRevenue'
]

# Build ordered periods list
ordered = []
for year in range(2015, 2026):
    # Add quarterly data for FY2020+
    if year >= 2020:
        for q in ['Q1', 'Q2', 'Q3']:
            p = f"{q} FY{year}"
            if p in all_periods:
                ordered.append(p)

    # Add annual
    fy = f"FY{year}"
    if fy in all_periods:
        ordered.append(fy)

# Add FY2026 Q1-Q2
for q in ['Q1', 'Q2']:
    p = f"{q} FY2026"
    if p in all_periods:
        ordered.append(p)

print(f"\nWriting {len(ordered)} periods to CSV...")
with open('/Users/swilliams/Stocks/Research/MSFT/Reports/MSFT_Metrics.csv', 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=columns)
    writer.writeheader()

    for period in ordered:
        row = {'Period': period}
        row.update(all_periods[period])
        writer.writerow(row)

print(f"Done! Sample periods:")
for p in ordered[:3] + ordered[-3:]:
    rev = all_periods[p].get('Revenue', 'N/A')
    print(f"  {p}: Revenue={rev}")
