#!/usr/bin/env python3
"""
Parse NVIDIA financial metrics from extracted SEC filings.
Final comprehensive version.
"""

import re
import csv
from pathlib import Path
from typing import Dict, Optional, List

# Directories
BASE_DIR = Path("/Users/swilliams/Stocks/Research/NVDA")
EXTRACTED_DIR = BASE_DIR / "Extracted"
REPORTS_DIR = BASE_DIR / "Reports"
OUTPUT_CSV = REPORTS_DIR / "NVDA_Metrics.csv"

# Ensure output directory exists
REPORTS_DIR.mkdir(exist_ok=True, parents=True)


def clean_number(text: str) -> Optional[float]:
    """Extract and clean a number from text."""
    if not text or text in ['—', '-', '', '$']:
        return None
    text = text.replace('$', '').replace(',', '').strip()
    is_negative = text.startswith('(') and text.endswith(')')
    if is_negative:
        text = text[1:-1].strip()
    try:
        value = float(text)
        return -value if is_negative else value
    except (ValueError, AttributeError):
        return None


def extract_from_line(content: str, pattern: str) -> Optional[float]:
    """Extract first number from a line matching pattern."""
    match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
    if match:
        numbers = re.findall(r'[\d,\(\)\.]+', match.group(0))
        for num_str in numbers:
            num = clean_number(num_str)
            if num is not None and abs(num) > 0.01:  # Skip tiny numbers (likely formatting)
                return num
    return None


def extract_annual(filepath: Path, fy: str) -> Dict:
    """Extract annual metrics from 10-K."""
    print(f"Processing FY{fy}...")
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    m = {'Period': f'FY{fy}'}

    # Find income statement section
    inc_start = re.search(r'Consolidated Statements? of Income', content, re.I)
    if not inc_start:
        return m
    inc_section = content[inc_start.start():inc_start.start()+3000]

    # Extract key values (first column after label)
    lines = inc_section.split('\n')
    for i, line in enumerate(lines):
        if 'Revenue' in line and 'Cost' not in line and 'Net' not in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 100]  # Filter valid revenue numbers
            if nums: m['Revenue'] = nums[0]
        elif 'Cost of revenue' in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 50]
            if nums: m['CostOfRevenue'] = nums[0]
        elif 'Gross profit' in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 100]
            if nums: m['GrossProfit'] = nums[0]
        elif 'Research and development' in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 50]
            if nums: m['ResearchDevelopment'] = nums[0]
        elif 'Sales, general and administrative' in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 50]
            if nums: m['SGA'] = nums[0]
        elif 'Operating income' in line and 'loss' not in line.lower():
            nums = [clean_number(s) for s in re.findall(r'[\d,\(\)]+', line)]
            nums = [n for n in nums if n and abs(n) > 50]
            if nums: m['OperatingIncome'] = nums[0]
        elif line.strip().startswith('Net income') and 'per share' not in line.lower():
            nums = [clean_number(s) for s in re.findall(r'[\d,\(\)]+', line)]
            nums = [n for n in nums if n and abs(n) > 50]
            if nums: m['NetIncome'] = nums[0]
        elif 'Diluted' in line and '$' in line and 'share' in line.lower():
            # EPS line
            eps_nums = re.findall(r'\$\s*([\d\.]+)', line)
            if eps_nums: m['DilutedEPS'] = float(eps_nums[0])
        elif 'Diluted' in line and 'Weighted' not in line and 'per share' not in line.lower():
            # Diluted shares line
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 1000]  # Shares in millions
            if nums: m['DilutedShares'] = nums[0]

    # Calculate margins
    if 'Revenue' in m and 'GrossProfit' in m and m['Revenue']:
        m['GrossMargin'] = round((m['GrossProfit'] / m['Revenue']) * 100, 1)
    if 'Revenue' in m and 'OperatingIncome' in m and m['Revenue']:
        m['OperatingMargin'] = round((m['OperatingIncome'] / m['Revenue']) * 100, 1)
    if 'Revenue' in m and 'NetIncome' in m and m['Revenue']:
        m['NetMargin'] = round((m['NetIncome'] / m['Revenue']) * 100, 1)
    if 'ResearchDevelopment' in m and 'SGA' in m:
        m['OperatingExpenses'] = m['ResearchDevelopment'] + m['SGA']

    # Balance sheet
    bal_start = re.search(r'Consolidated Balance Sheets?', content, re.I)
    if bal_start:
        bal_section = content[bal_start.start():bal_start.start()+2500]
        for line in bal_section.split('\n'):
            if 'Total assets' in line and 'Current' not in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 1000]
                if nums: m['TotalAssets'] = nums[0]
            elif 'Total liabilities' in line and 'Current' not in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 1000]
                if nums: m['TotalLiabilities'] = nums[0]
            elif 'Total shareholders\' equity' in line or 'Total stockholders\' equity' in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 1000]
                if nums: m['ShareholdersEquity'] = nums[0]
            elif line.strip().startswith('Cash and cash equivalents') and 'Total' not in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 100]
                if nums: m['Cash'] = nums[0]
            elif 'Marketable securities' in line and 'Total' not in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 100]
                if nums:
                    if 'Cash' in m:
                        m['CashAndInvestments'] = m['Cash'] + nums[0]
            elif 'Long-term debt' in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 100]
                if nums:
                    m['TotalDebt'] = m.get('TotalDebt', 0) + nums[0]
            elif 'Short-term debt' in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 100]
                if nums:
                    m['TotalDebt'] = m.get('TotalDebt', 0) + nums[0]

        # Shares outstanding from balance sheet header
        shares_match = re.search(r'([\d,]+)\s+shares issued and outstanding', bal_section)
        if shares_match:
            shares = clean_number(shares_match.group(1))
            if shares: m['SharesOutstanding'] = shares

    # Cash flow
    cf_start = re.search(r'Consolidated Statements? of Cash Flows?', content, re.I)
    if cf_start:
        cf_section = content[cf_start.start():cf_start.start()+2500]
        for line in cf_section.split('\n'):
            if 'Net cash provided by operating activities' in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,\(\)]+', line)]
                nums = [n for n in nums if n and abs(n) > 100]
                if nums: m['OperatingCashFlow'] = nums[0]
            elif 'Purchases related to property and equipment' in line or 'Capital expenditure' in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,\(\)]+', line)]
                nums = [n for n in nums if n and abs(n) > 10]
                if nums: m['CapEx'] = abs(nums[0])

        if 'OperatingCashFlow' in m and 'CapEx' in m:
            m['FreeCashFlow'] = m['OperatingCashFlow'] - m['CapEx']

    # Segment/Market revenue
    # Look for "Compute & Networking" and "Graphics"
    seg_match = re.search(r'Compute\s*&\s*Networking\s+\$?\s*([\d,]+)', content, re.I)
    if seg_match:
        m['ComputeNetworkingRevenue'] = clean_number(seg_match.group(1))

    seg_match2 = re.search(r'Graphics\s+\$?\s*([\d,]+)(?:\s+\$?\s*[\d,]+){0,2}\s*(?:\n|$)', content, re.I)
    if seg_match2:
        m['GraphicsRevenue'] = clean_number(seg_match2.group(1))

    # Market breakdown
    dc_match = re.search(r'Data Center\s+\$?\s*([\d,]+)', content, re.I)
    if dc_match:
        m['DataCenterRevenue'] = clean_number(dc_match.group(1))

    gam_match = re.search(r'Gaming\s+\$?\s*([\d,]+)', content, re.I)
    if gam_match and 'GamingRevenue' not in m:
        m['GamingRevenue'] = clean_number(gam_match.group(1))

    print(f"  FY{fy}: Revenue=${m.get('Revenue', 'N/A')}M, NI=${m.get('NetIncome', 'N/A')}M")
    return m


def extract_quarterly(filepath: Path, q: str, fy: str) -> Dict:
    """Extract quarterly metrics from 10-Q."""
    print(f"Processing Q{q} FY{fy}...")
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    m = {'Period': f'Q{q} FY{fy}'}

    # Similar logic to annual but looking for "Three Months Ended" section
    inc_start = re.search(r'Condensed Consolidated Statements? of Income', content, re.I)
    if not inc_start:
        return m

    # Look for section with quarterly data
    inc_section = content[inc_start.start():inc_start.start()+2000]

    lines = inc_section.split('\n')
    for i, line in enumerate(lines):
        if 'Revenue' in line and 'Cost' not in line and 'Net' not in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 50]
            if nums: m['Revenue'] = nums[0]
        elif 'Cost of revenue' in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 20]
            if nums: m['CostOfRevenue'] = nums[0]
        elif 'Gross profit' in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 50]
            if nums: m['GrossProfit'] = nums[0]
        elif 'Research and development' in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 20]
            if nums: m['ResearchDevelopment'] = nums[0]
        elif 'Sales, general and administrative' in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 20]
            if nums: m['SGA'] = nums[0]
        elif 'Operating income' in line:
            nums = [clean_number(s) for s in re.findall(r'[\d,\(\)]+', line)]
            nums = [n for n in nums if n and abs(n) > 20]
            if nums: m['OperatingIncome'] = nums[0]
        elif line.strip().startswith('Net income') and 'per share' not in line.lower():
            nums = [clean_number(s) for s in re.findall(r'[\d,\(\)]+', line)]
            nums = [n for n in nums if n and abs(n) > 20]
            if nums: m['NetIncome'] = nums[0]
        elif 'Diluted' in line and '$' in line and 'share' in line.lower():
            eps_nums = re.findall(r'\$\s*([\d\.]+)', line)
            if eps_nums: m['DilutedEPS'] = float(eps_nums[0])
        elif 'Diluted' in line and 'Weighted' not in line and 'per share' not in line.lower():
            nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
            nums = [n for n in nums if n and n > 1000]
            if nums: m['DilutedShares'] = nums[0]

    # Calculate margins
    if 'Revenue' in m and 'GrossProfit' in m and m['Revenue']:
        m['GrossMargin'] = round((m['GrossProfit'] / m['Revenue']) * 100, 1)
    if 'Revenue' in m and 'OperatingIncome' in m and m['Revenue']:
        m['OperatingMargin'] = round((m['OperatingIncome'] / m['Revenue']) * 100, 1)
    if 'Revenue' in m and 'NetIncome' in m and m['Revenue']:
        m['NetMargin'] = round((m['NetIncome'] / m['Revenue']) * 100, 1)
    if 'ResearchDevelopment' in m and 'SGA' in m:
        m['OperatingExpenses'] = m['ResearchDevelopment'] + m['SGA']

    # Balance sheet (point in time at quarter end)
    bal_start = re.search(r'Condensed Consolidated Balance Sheets?', content, re.I)
    if bal_start:
        bal_section = content[bal_start.start():bal_start.start()+2000]
        for line in bal_section.split('\n'):
            if 'Total assets' in line and 'Current' not in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 1000]
                if nums: m['TotalAssets'] = nums[0]
            elif 'Total liabilities' in line and 'Current' not in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 1000]
                if nums: m['TotalLiabilities'] = nums[0]
            elif 'Total shareholders\' equity' in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 1000]
                if nums: m['ShareholdersEquity'] = nums[0]
            elif line.strip().startswith('Cash and cash equivalents'):
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 100]
                if nums: m['Cash'] = nums[0]
            elif 'Marketable securities' in line and 'Total' not in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 100]
                if nums and 'Cash' in m:
                    m['CashAndInvestments'] = m['Cash'] + nums[0]
            elif 'Long-term debt' in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 100]
                if nums: m['TotalDebt'] = m.get('TotalDebt', 0) + nums[0]
            elif 'Short-term debt' in line:
                nums = [clean_number(s) for s in re.findall(r'[\d,]+', line)]
                nums = [n for n in nums if n and n > 100]
                if nums: m['TotalDebt'] = m.get('TotalDebt', 0) + nums[0]

        shares_match = re.search(r'([\d,\.]+)\s+shares issued and outstanding', bal_section)
        if shares_match:
            shares = clean_number(shares_match.group(1))
            if shares: m['SharesOutstanding'] = shares

    # Segments
    seg_match = re.search(r'Compute\s*&?\s*Networking\s+\$?\s*([\d,]+)', content, re.I)
    if seg_match:
        m['ComputeNetworkingRevenue'] = clean_number(seg_match.group(1))

    seg_match2 = re.search(r'Graphics\s+\$?\s*([\d,]+)', content, re.I)
    if seg_match2:
        m['GraphicsRevenue'] = clean_number(seg_match2.group(1))

    print(f"  Q{q} FY{fy}: Revenue=${m.get('Revenue', 'N/A')}M, NI=${m.get('NetIncome', 'N/A')}M")
    return m


def main():
    """Main execution."""
    print("="*70)
    print("NVIDIA Financial Metrics Parser")
    print("="*70)

    all_metrics = []

    # Process annual 10-K files
    print("\n[ANNUAL REPORTS]")
    for year in range(2015, 2026):
        filepath = EXTRACTED_DIR / f"NVDA_10K_FY{year}.txt"
        if filepath.exists():
            try:
                metrics = extract_annual(filepath, str(year))
                all_metrics.append(metrics)
            except Exception as e:
                print(f"  ERROR: {e}")

    # Process quarterly 10-Q files
    print("\n[QUARTERLY REPORTS]")
    for year in range(2015, 2027):
        for q in [1, 2, 3]:
            filepath = EXTRACTED_DIR / f"NVDA_10Q_Q{q}-FY{year}.txt"
            if filepath.exists():
                try:
                    metrics = extract_quarterly(filepath, str(q), str(year))
                    all_metrics.append(metrics)
                except Exception as e:
                    print(f"  ERROR: {e}")

    # Sort chronologically
    def sort_key(m):
        p = m['Period']
        if p.startswith('FY'):
            return (int(p[2:]), 4)
        else:
            parts = p.split()
            return (int(parts[1][2:]), int(parts[0][1]))

    all_metrics.sort(key=sort_key)

    # Write CSV
    columns = [
        'Period', 'Revenue', 'CostOfRevenue', 'GrossProfit', 'GrossMargin',
        'OperatingExpenses', 'ResearchDevelopment', 'SGA',
        'OperatingIncome', 'OperatingMargin', 'NetIncome', 'NetMargin',
        'DilutedEPS', 'DilutedShares',
        'FreeCashFlow', 'OperatingCashFlow', 'CapEx',
        'ComputeNetworkingRevenue', 'GraphicsRevenue',
        'DataCenterRevenue', 'GamingRevenue',
        'ProfessionalVisualizationRevenue', 'AutomotiveRevenue',
        'TotalAssets', 'TotalLiabilities', 'ShareholdersEquity',
        'Cash', 'CashAndInvestments', 'TotalDebt', 'SharesOutstanding'
    ]

    with open(OUTPUT_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()

        for m in all_metrics:
            row = {}
            for col in columns:
                val = m.get(col)
                if val is not None:
                    if col == 'Period':
                        row[col] = val
                    elif col in ['GrossMargin', 'OperatingMargin', 'NetMargin']:
                        row[col] = val
                    elif col == 'DilutedEPS':
                        row[col] = round(val, 2)
                    else:
                        row[col] = round(val, 1)
                else:
                    row[col] = ''
            writer.writerow(row)

    print("\n" + "="*70)
    print(f"✓ CSV created with {len(all_metrics)} periods")
    print(f"  Output: {OUTPUT_CSV}")
    print("="*70)

    # Summary
    print("\nSummary:")
    for m in all_metrics[-10:]:  # Last 10 periods
        p = m.get('Period', '?')
        r = m.get('Revenue')
        n = m.get('NetIncome')
        r_str = f"${r:>10.1f}M" if r else "N/A"
        n_str = f"${n:>10.1f}M" if n else "N/A"
        print(f"  {p:15s}  Rev: {r_str}  NI: {n_str}")


if __name__ == '__main__':
    main()
