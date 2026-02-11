#!/usr/bin/env python3
"""
Parse BYD Electronic (0285.HK) financial data from extracted text files
"""

import re
import os
from pathlib import Path

# Base directory
BASE_DIR = Path("/Users/swilliams/Stocks/Research/0285.HK/Extracted")

def extract_number(text, multiplier=1):
    """Extract number from text, handling thousands separator"""
    if not text:
        return None
    # Remove commas and convert
    text = text.replace(',', '').strip()
    try:
        return float(text) * multiplier
    except:
        return None

def parse_h1_2024(filepath):
    """Parse H1 2024 interim report"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    data = {
        'Period': 'H1 2024',
        'Revenue': None,
        'GrossProfit': None,
        'NetProfit': None,
        'EPS': None,
        'ShareholdersEquity': None,
        'TotalDebt': None,
        'CashAndEquivalents': None,
    }

    # Search for key financial data from the statements
    # Revenue: 78,580,818 thousand RMB (line 1214)
    rev_match = re.search(r'REVENUE 收入.*?(\d+),(\d+),(\d+)\s+(\d+),(\d+),(\d+)', content, re.DOTALL)
    if rev_match:
        # First set of numbers is 2024
        data['Revenue'] = float(f"{rev_match.group(1)}{rev_match.group(2)}{rev_match.group(3)}")

    # Gross profit: 5,379,032 thousand RMB
    gp_match = re.search(r'Gross profit 毛利\s+(\d+),(\d+),(\d+)', content)
    if gp_match:
        data['GrossProfit'] = float(f"{gp_match.group(1)}{gp_match.group(2)}{gp_match.group(3)}")

    # Net profit attributable to owners: 1,517,800 thousand RMB
    np_match = re.search(r'Attributable to owners of the parent 母公司擁有人應佔\s+(\d+),(\d+),(\d+)', content)
    if np_match:
        data['NetProfit'] = float(f"{np_match.group(1)}{np_match.group(2)}{np_match.group(3)}")

    # EPS: RMB 0.67
    eps_match = re.search(r'RMB人民幣([\d.]+)元.*RMB人民幣([\d.]+)元', content)
    if eps_match:
        data['EPS'] = float(eps_match.group(1))

    # Total equity: 29,644,966 thousand RMB (as at 30 June 2024)
    equity_match = re.search(r'Total equity 權益總額\s+(\d+),(\d+),(\d+)', content)
    if equity_match:
        data['ShareholdersEquity'] = float(f"{equity_match.group(1)}{equity_match.group(2)}{equity_match.group(3)}")

    # Interest-bearing loans (current): 15,575,792 thousand RMB
    debt_match = re.search(r'Interest-bearing loans 計息貸款\s+(\d+),(\d+),(\d+)', content)
    if debt_match:
        data['TotalDebt'] = float(f"{debt_match.group(1)}{debt_match.group(2)}{debt_match.group(3)}")

    # Cash and cash equivalents: 8,130,150 thousand RMB
    cash_match = re.search(r'Cash and cash equivalents 現金及現金等價物\s+(\d+),(\d+),(\d+)', content)
    if cash_match:
        data['CashAndEquivalents'] = float(f"{cash_match.group(1)}{cash_match.group(2)}{cash_match.group(3)}")

    return data

def parse_all_files():
    """Parse all extracted text files"""

    results = []

    # H1 2024
    h1_2024_file = BASE_DIR / "0285.HK_Interim_H1-2024.txt"
    if h1_2024_file.exists():
        print(f"Parsing {h1_2024_file.name}...")
        data = parse_h1_2024(h1_2024_file)
        results.append(data)
        print(f"  Revenue: {data['Revenue']}")
        print(f"  Net Profit: {data['NetProfit']}")
        print(f"  EPS: {data['EPS']}")
        print(f"  Equity: {data['ShareholdersEquity']}")

    return results

if __name__ == "__main__":
    results = parse_all_files()

    print("\n=== Extracted Data ===")
    for data in results:
        print(f"\n{data['Period']}:")
        for key, value in data.items():
            if key != 'Period':
                print(f"  {key}: {value}")
