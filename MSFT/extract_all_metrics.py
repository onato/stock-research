#!/usr/bin/env python3
"""
Extract MSFT metrics from all 10-Q and 10-K files
"""
import re
import os
from pathlib import Path

def extract_three_months_data(file_path):
    """Extract three months ended data from 10-Q"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the income statement section
    income_match = re.search(r'INCOME STATEMENTS.*?Three Months Ended.*?\n(.*?)\nRefer to accompanying notes', content, re.DOTALL)
    if not income_match:
        return None

    income_section = income_match.group(1)

    # Extract numbers - look for patterns with dollar amounts
    lines = income_section.split('\n')

    data = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if 'Total revenue' in line:
            # Next line should have current period number
            if i + 1 < len(lines):
                try:
                    data['Revenue'] = int(lines[i+1].strip().replace(',', ''))
                except:
                    pass

        elif 'Gross margin' in line and 'percentage' not in line.lower():
            if i + 1 < len(lines):
                try:
                    data['GrossProfit'] = int(lines[i+1].strip().replace(',', ''))
                except:
                    pass

        elif 'Operating income' in line:
            if i + 1 < len(lines):
                try:
                    data['OperatingIncome'] = int(lines[i+1].strip().replace(',', ''))
                except:
                    pass

        elif 'Net income' == line and '$' in lines[i+1] if i+1 < len(lines) else False:
            if i + 2 < len(lines):
                try:
                    data['NetIncome'] = int(lines[i+2].strip().replace(',', ''))
                except:
                    pass

        elif 'Diluted' in line and 'shares' not in line.lower():
            # Look for EPS
            if i + 2 < len(lines):
                try:
                    eps_str = lines[i+2].strip().replace(',', '')
                    data['EPS'] = float(eps_str)
                except:
                    pass

        i += 1

    # Extract segment revenue
    segment_match = re.search(r'Revenue.*?Productivity and Business Processes.*?\$\s*\n([\d,]+).*?Intelligent Cloud.*?\n([\d,]+).*?More Personal Computing.*?\n([\d,]+)', content, re.DOTALL)
    if segment_match:
        try:
            data['ProductivityRevenue'] = int(segment_match.group(1).replace(',', ''))
            data['CloudRevenue'] = int(segment_match.group(2).replace(',', ''))
            data['PersonalComputingRevenue'] = int(segment_match.group(3).replace(',', ''))
        except:
            pass

    # Try alternative segment pattern
    if 'ProductivityRevenue' not in data:
        alt_segment = re.search(r'Productivity and Business Processes.*?Revenue.*?\$\s*([\d,]+).*?Intelligent Cloud.*?Revenue.*?\$\s*([\d,]+).*?More Personal Computing.*?Revenue.*?\$\s*([\d,]+)', content, re.DOTALL)
        if alt_segment:
            try:
                data['ProductivityRevenue'] = int(alt_segment.group(1).replace(',', ''))
                data['CloudRevenue'] = int(alt_segment.group(2).replace(',', ''))
                data['PersonalComputingRevenue'] = int(alt_segment.group(3).replace(',', ''))
            except:
                pass

    return data

def extract_annual_data(file_path):
    """Extract annual data from 10-K"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    data = {}

    # Look for SEGMENT RESULTS section which has cleaner data
    segment_match = re.search(r'SEGMENT RESULTS OF OPERATIONS.*?Productivity and Business Processes.*?Revenue.*?\$\s*([\d,]+).*?Intelligent Cloud.*?Revenue.*?\$\s*([\d,]+).*?More Personal Computing.*?Revenue.*?\$\s*([\d,]+)', content, re.DOTALL)
    if segment_match:
        try:
            data['ProductivityRevenue'] = int(segment_match.group(1).replace(',', ''))
            data['CloudRevenue'] = int(segment_match.group(2).replace(',', ''))
            data['PersonalComputingRevenue'] = int(segment_match.group(3).replace(',', ''))
        except:
            pass

    # Find Total revenue and other metrics
    income_match = re.search(r'CONSOLIDATED INCOME STATEMENTS.*?Year Ended June 30,.*?\n(.*?)\nSee accompanying notes', content, re.DOTALL)
    if income_match:
        income_section = income_match.group(1)
        lines = income_section.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if 'Total revenue' in line:
                if i + 1 < len(lines):
                    try:
                        data['Revenue'] = int(lines[i+1].strip().replace(',', ''))
                    except:
                        pass

            elif 'Gross margin' in line:
                if i + 1 < len(lines):
                    try:
                        data['GrossProfit'] = int(lines[i+1].strip().replace(',', ''))
                    except:
                        pass

            elif 'Operating income' in line:
                if i + 1 < len(lines):
                    try:
                        data['OperatingIncome'] = int(lines[i+1].strip().replace(',', ''))
                    except:
                        pass

            elif 'Net income' == line:
                if i + 2 < len(lines):
                    try:
                        data['NetIncome'] = int(lines[i+2].strip().replace(',', ''))
                    except:
                        pass

            i += 1

    # Find cash flow data
    cashflow_match = re.search(r'CONSOLIDATED CASH FLOWS STATEMENTS.*?Year Ended June 30,.*?\n(.*?)CONSOLIDATED BALANCE SHEETS', content, re.DOTALL)
    if cashflow_match:
        cf_section = cashflow_match.group(1)

        # Operating cash flow
        ocf_match = re.search(r'Net cash from operations.*?\$\s*([\d,]+)', cf_section)
        if ocf_match:
            try:
                data['OperatingCashFlow'] = int(ocf_match.group(1).replace(',', ''))
            except:
                pass

        # CapEx
        capex_match = re.search(r'Additions to property and equipment.*?\(([\d,]+)\)', cf_section)
        if capex_match:
            try:
                data['CapEx'] = int(capex_match.group(1).replace(',', ''))
            except:
                pass

    # Find balance sheet data
    bs_match = re.search(r'CONSOLIDATED BALANCE SHEETS.*?June 30,.*?\n(.*?)CONSOLIDATED CASH FLOWS', content, re.DOTALL)
    if bs_match:
        bs_section = bs_match.group(1)

        # Shareholders equity
        equity_match = re.search(r'Total stockholders\' equity.*?\$\s*([\d,]+)', bs_section)
        if equity_match:
            try:
                data['ShareholdersEquity'] = int(equity_match.group(1).replace(',', ''))
            except:
                pass

        # Cash
        cash_match = re.search(r'Cash and cash equivalents.*?\$\s*([\d,]+)', bs_section)
        if cash_match:
            try:
                data['CashAndEquivalents'] = int(cash_match.group(1).replace(',', ''))
            except:
                pass

        # Total debt - need to sum current and long-term
        current_debt_match = re.search(r'Current portion of long-term debt.*?\$\s*([\d,]+)', bs_section)
        longterm_debt_match = re.search(r'Long-term debt.*?\$\s*([\d,]+)', bs_section)
        total_debt = 0
        if current_debt_match:
            try:
                total_debt += int(current_debt_match.group(1).replace(',', ''))
            except:
                pass
        if longterm_debt_match:
            try:
                total_debt += int(longterm_debt_match.group(1).replace(',', ''))
            except:
                pass
        if total_debt > 0:
            data['TotalDebt'] = total_debt

    return data

# Test with one file
test_file = '/Users/swilliams/Stocks/Research/MSFT/Extracted/MSFT_10Q_Q1-FY2020.txt'
print("Testing Q1 FY2020:")
print(extract_three_months_data(test_file))

test_annual = '/Users/swilliams/Stocks/Research/MSFT/Extracted/MSFT_10K_FY2025.txt'
print("\nTesting FY2025:")
print(extract_annual_data(test_annual))
