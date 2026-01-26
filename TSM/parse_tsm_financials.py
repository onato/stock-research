#!/usr/bin/env python3
"""
TSM Financial Metrics Parser
Extracts financial metrics from TSM 20-F and 6-K extracted text files
"""

import re
import csv
from pathlib import Path
from typing import Dict, Optional, List
import sys

class TSMParser:
    def __init__(self, extracted_dir: str):
        self.extracted_dir = Path(extracted_dir)
        self.metrics = []

    def parse_all_files(self):
        """Parse all 20-F and 6-K files"""
        # Get all files
        files = sorted(self.extracted_dir.glob("TSM_*.txt"))

        for file in files:
            print(f"Processing {file.name}...")

            if "20-F" in file.name:
                # Annual report
                period = self.extract_period_from_filename(file.name, is_annual=True)
                data = self.parse_annual_report(file)
                if data:
                    data['Period'] = period
                    self.metrics.append(data)
            elif "6-K" in file.name:
                # Quarterly report
                period = self.extract_period_from_filename(file.name, is_annual=False)
                data = self.parse_quarterly_report(file)
                if data:
                    data['Period'] = period
                    self.metrics.append(data)

    def extract_period_from_filename(self, filename: str, is_annual: bool) -> str:
        """Extract period from filename"""
        if is_annual:
            # TSM_20-F_FY2024.txt -> FY2024
            match = re.search(r'FY(\d{4})', filename)
            if match:
                return f"FY{match.group(1)}"
        else:
            # TSM_6-K_Q2-2024.txt -> Q2 2024
            match = re.search(r'Q(\d)-(\d{4})', filename)
            if match:
                return f"Q{match.group(1)} {match.group(2)}"
        return ""

    def parse_annual_report(self, filepath: Path) -> Optional[Dict]:
        """Parse 20-F annual report"""
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        data = {}

        # Find the consolidated statements sections
        # Income Statement
        income_match = re.search(
            r'CONSOLIDATED STATEMENTS OF PROFIT OR LOSS.*?'
            r'NET REVENUE.*?\$?\s*[\d,]+\.?\d*.*?'
            r'COST OF REVENUE.*?\$?\s*[\d,]+\.?\d*.*?'
            r'GROSS PROFIT.*?\$?\s*[\d,]+\.?\d*',
            content, re.DOTALL
        )

        # Try to find the most recent year's data in the income statement
        # Pattern: Look for three years of data (2022, 2023, 2024 for FY2024 report)

        # Extract Revenue (Net Revenue) - looking for the latest year (rightmost column)
        revenue_pattern = r'NET REVENUE.*?[\r\n]+.*?[\r\n]+.*?\$\s*([\d,]+\.?\d*)\s+[\r\n]'
        rev_matches = list(re.finditer(revenue_pattern, content, re.DOTALL))
        if rev_matches:
            # Get the last instance which is likely the summary table
            last_match = rev_matches[-1]
            # Extract all numbers from the NET REVENUE section
            section = content[last_match.start():last_match.start()+500]
            numbers = re.findall(r'\$?\s*([\d,]+\.\d+)\s', section)
            if len(numbers) >= 3:
                # Latest year is typically the third number (or second in NT$)
                data['Revenue'] = self.to_billions(numbers[-2])  # Second to last in NT$

        # Extract from specific patterns in consolidated statements
        # Look for year headers to identify which column is which
        year_header_pattern = r'(2022|2023|2024)[\r\n].*?[\r\n].*?NT\$'

        # Find the financial statement tables more precisely
        bal_sheet_start = content.find('CONSOLIDATED STATEMENTS OF FINANCIAL POSITION')
        income_start = content.find('CONSOLIDATED STATEMENTS OF PROFIT OR LOSS')
        cash_flow_start = content.find('CONSOLIDATED STATEMENTS OF CASH FLOWS')

        if income_start > 0:
            # Extract from income statement (next ~3000 characters)
            income_section = content[income_start:income_start+5000]

            # Get the latest year data
            data.update(self.extract_income_statement(income_section))

        if bal_sheet_start > 0:
            bal_section = content[bal_sheet_start:bal_sheet_start+5000]
            data.update(self.extract_balance_sheet(bal_section))

        if cash_flow_start > 0:
            cash_section = content[cash_flow_start:cash_flow_start+5000]
            data.update(self.extract_cash_flow(cash_section))

        # Extract EPS
        eps_pattern = r'Basic earnings per share.*?\$\s*([\d.]+)'
        eps_matches = re.findall(eps_pattern, content)
        if eps_matches:
            data['EPS'] = eps_matches[-1]  # Latest year

        # Extract shares outstanding
        shares_pattern = r'Weighted average number of common shares.*?\(in millions\).*?([\d,]+\.?\d*)'
        shares_matches = re.findall(shares_pattern, content, re.DOTALL)
        if shares_matches:
            # Convert to billions
            shares_millions = shares_matches[-1].replace(',', '')
            data['SharesOutstanding'] = str(round(float(shares_millions) / 1000, 3))

        return data if data else None

    def extract_income_statement(self, section: str) -> Dict:
        """Extract income statement items from a section"""
        data = {}

        # Find all NT$ amounts in order
        # Pattern: Line name followed by amounts
        lines = {
            'Revenue': r'NET REVENUE',
            'CostOfRevenue': r'COST OF REVENUE',
            'GrossProfit': r'GROSS PROFIT',
            'RnDExpense': r'Research and development',
            'OperatingIncome': r'INCOME FROM OPERATIONS',
            'NetIncome': r'NET INCOME(?!\s+BEFORE)'
        }

        for key, pattern in lines.items():
            match = re.search(
                pattern + r'[^\d\n]*?([\d,]+\.?\d*)\s+[^\d\n]*?([\d,]+\.?\d*)\s+[^\d\n]*?([\d,]+\.?\d*)',
                section
            )
            if match:
                # Third number is usually the latest year
                value = match.group(3).replace(',', '')
                if key in ['Revenue', 'CostOfRevenue', 'GrossProfit', 'OperatingIncome', 'NetIncome']:
                    data[key] = self.to_billions(value)
                elif key == 'RnDExpense':
                    data['RnDExpense'] = self.to_billions(value)

        # Calculate margins if we have the data
        if 'Revenue' in data and 'GrossProfit' in data:
            try:
                data['GrossMargin'] = str(round(float(data['GrossProfit']) / float(data['Revenue']) * 100, 1))
            except:
                pass

        if 'Revenue' in data and 'OperatingIncome' in data:
            try:
                data['OperatingMargin'] = str(round(float(data['OperatingIncome']) / float(data['Revenue']) * 100, 1))
            except:
                pass

        if 'Revenue' in data and 'NetIncome' in data:
            try:
                data['NetMargin'] = str(round(float(data['NetIncome']) / float(data['Revenue']) * 100, 1))
            except:
                pass

        return data

    def extract_balance_sheet(self, section: str) -> Dict:
        """Extract balance sheet items"""
        data = {}

        lines = {
            'TotalAssets': r'TOTAL(?:\s+ASSETS|\s*$)',
            'CashAndEquivalents': r'Cash and cash equivalents',
            'ShareholdersEquity': r'Total equity|Equity attributable to shareholders',
            'TotalLiabilities': r'Total liabilities'
        }

        for key, pattern in lines.items():
            # Look for the pattern followed by numbers
            match = re.search(
                pattern + r'[^\d\n]*?[\d,]+\.?\d*[^\d\n]*?([\d,]+\.?\d*)',
                section,
                re.IGNORECASE
            )
            if match:
                value = match.group(1).replace(',', '')
                data[key] = self.to_billions(value)

        # Extract debt - look for bonds payable and bank loans
        bonds_match = re.search(r'Bonds payable[^\d\n]*?([\d,]+\.?\d*)', section)
        loans_match = re.search(r'Long-term bank loans[^\d\n]*?([\d,]+\.?\d*)', section)

        total_debt = 0
        if bonds_match:
            total_debt += float(bonds_match.group(1).replace(',', ''))
        if loans_match:
            total_debt += float(loans_match.group(1).replace(',', ''))

        if total_debt > 0:
            data['TotalDebt'] = self.to_billions(str(total_debt))

        return data

    def extract_cash_flow(self, section: str) -> Dict:
        """Extract cash flow items"""
        data = {}

        # Operating cash flow
        ocf_match = re.search(
            r'Net cash generated by operating activities[^\d\n]*?([\d,]+\.?\d*)',
            section
        )
        if ocf_match:
            data['OperatingCashFlow'] = self.to_billions(ocf_match.group(1))

        # CapEx - look for acquisition of property, plant and equipment
        capex_match = re.search(
            r'Property, plant and equipment[^\d\n]*?\(\s*([\d,]+\.?\d*)\s*\)',
            section
        )
        if capex_match:
            data['CapEx'] = self.to_billions(capex_match.group(1))

        # Calculate FCF
        if 'OperatingCashFlow' in data and 'CapEx' in data:
            try:
                fcf = float(data['OperatingCashFlow']) - float(data['CapEx'])
                data['FreeCashFlow'] = str(round(fcf, 1))
            except:
                pass

        return data

    def parse_quarterly_report(self, filepath: Path) -> Optional[Dict]:
        """Parse 6-K quarterly report"""
        # Quarterly reports have similar structure but less detail
        # For now, return minimal data or skip
        return None

    def to_billions(self, value_str: str) -> str:
        """Convert millions to billions (NT$)"""
        try:
            value = float(value_str.replace(',', ''))
            # Values in financial statements are in millions
            billions = value / 1000
            return str(round(billions, 1))
        except:
            return ""

    def write_csv(self, output_file: str):
        """Write metrics to CSV"""
        if not self.metrics:
            print("No metrics to write")
            return

        # Sort by period
        self.metrics.sort(key=lambda x: self.period_sort_key(x.get('Period', '')))

        # Define all possible columns
        columns = [
            'Period', 'Revenue', 'GrossProfit', 'GrossMargin',
            'OperatingIncome', 'OperatingMargin', 'NetIncome', 'NetMargin',
            'EPS', 'TotalAssets', 'ShareholdersEquity', 'TotalDebt',
            'CashAndEquivalents', 'SharesOutstanding',
            'OperatingCashFlow', 'CapEx', 'FreeCashFlow',
            'RnDExpense'
        ]

        with open(output_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()

            for metric in self.metrics:
                # Fill in missing values with empty string
                row = {col: metric.get(col, '') for col in columns}
                writer.writerow(row)

        print(f"Wrote {len(self.metrics)} rows to {output_file}")

    def period_sort_key(self, period: str) -> tuple:
        """Generate sort key for period"""
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 5, 0)  # Annual reports after Q4
        elif period.startswith('Q'):
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter, 0)
        return (0, 0, 0)


def main():
    extracted_dir = "/Users/swilliams/Stocks/Research/TSM/Extracted"
    output_file = "/Users/swilliams/Stocks/Research/TSM/Reports/TSM_Metrics.csv"

    parser = TSMParser(extracted_dir)
    parser.parse_all_files()
    parser.write_csv(output_file)


if __name__ == "__main__":
    main()
