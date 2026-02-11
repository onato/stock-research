#!/usr/bin/env python3
"""
Parse Meta Platforms financial metrics from extracted SEC filings.
Extracts data from both 10-K (annual) and 10-Q (quarterly) reports.
"""

import re
import os
import csv
from pathlib import Path
from typing import Dict, List, Optional
from collections import OrderedDict

# Base directory
BASE_DIR = Path("/Users/swilliams/Stocks/Research/META")
EXTRACTED_DIR = BASE_DIR / "Extracted"
REPORTS_DIR = BASE_DIR / "Reports"
OUTPUT_CSV = REPORTS_DIR / "META_Metrics.csv"

# Ensure Reports directory exists
REPORTS_DIR.mkdir(exist_ok=True)


class MetricsExtractor:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.content = self._read_file()
        self.metrics = OrderedDict()

        # Determine period from filename
        self.metrics['Period'] = self._get_period()

    def _read_file(self) -> str:
        """Read the entire file content."""
        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    def _get_period(self) -> str:
        """Extract period from filename."""
        filename = self.file_path.name

        if '10K' in filename:
            # Extract year from FY2024 format
            match = re.search(r'FY(\d{4})', filename)
            if match:
                return f"FY{match.group(1)}"
        elif '10Q' in filename:
            # Extract quarter and year from Q1-2025 format
            match = re.search(r'(Q\d)-(\d{4})', filename)
            if match:
                return f"{match.group(1)} {match.group(2)}"

        return "Unknown"

    def _find_value(self, pattern: str, multiplier: float = 1.0) -> Optional[float]:
        """Find a numeric value using a regex pattern."""
        match = re.search(pattern, self.content, re.IGNORECASE | re.MULTILINE)
        if match:
            # Extract numeric value, removing commas and $
            value_str = match.group(1).replace(',', '').replace('$', '').strip()
            try:
                return float(value_str) * multiplier
            except ValueError:
                return None
        return None

    def _extract_from_income_statement(self):
        """Extract metrics from income statement (3-month period for quarterly)."""
        # Find the condensed consolidated statements of income section
        income_pattern = r'CONDENSED CONSOLIDATED STATEMENTS OF INCOME.*?(?=CONDENSED CONSOLIDATED|Table of Contents|See Accompanying)'
        income_match = re.search(income_pattern, self.content, re.DOTALL | re.IGNORECASE)

        if not income_match:
            income_pattern = r'CONSOLIDATED STATEMENTS OF INCOME.*?(?=CONSOLIDATED STATEMENTS OF COMPREHENSIVE|Table of Contents|See Accompanying)'
            income_match = re.search(income_pattern, self.content, re.DOTALL | re.IGNORECASE)

        if income_match:
            income_section = income_match.group(0)

            # For quarterly reports, we need THREE MONTHS data (not six months or cumulative)
            # Look for "Three Months Ended" section
            if 'Q' in self.metrics['Period']:
                # Try to find three months data
                three_months_pattern = r'Three Months Ended.*?(?:March 31|June 30|September 30|December 31).*?\n.*?(\d{4}).*?\n.*?Revenue.*?\$\s*([\d,]+)'
                match = re.search(three_months_pattern, income_section, re.DOTALL | re.IGNORECASE)

                if match:
                    # Get the year and revenue
                    year = match.group(1)
                    revenue = match.group(2).replace(',', '')

                    # Make sure this matches our expected period
                    period_year = self.metrics['Period'].split()[-1]
                    if year == period_year:
                        self.metrics['Revenue'] = float(revenue)

            # For annual reports or if we haven't found quarterly data
            if 'Revenue' not in self.metrics:
                # Look for year ended pattern
                if 'FY' in self.metrics['Period']:
                    year = self.metrics['Period'].replace('FY', '')
                    year_pattern = rf'(?:Year|Years) Ended.*?December 31.*?\n.*?{year}.*?\n.*?Revenue.*?\$\s*([\d,]+)'
                    match = re.search(year_pattern, income_section, re.DOTALL | re.IGNORECASE)
                    if match:
                        self.metrics['Revenue'] = float(match.group(1).replace(',', ''))

            # Extract other income statement items using similar logic
            # Operating income
            if 'FY' in self.metrics['Period']:
                year = self.metrics['Period'].replace('FY', '')
                op_income_pattern = rf'Income from operations.*?\$\s*([\d,]+).*?\$\s*([\d,]+).*?\$\s*([\d,]+)'
                match = re.search(op_income_pattern, income_section, re.DOTALL)
                if match:
                    # Try to identify which column is our year
                    self.metrics['OperatingIncome'] = float(match.group(1).replace(',', ''))

            # Net income and EPS
            net_income_pattern = r'Net income.*?\$\s*([\d,]+)'
            match = re.search(net_income_pattern, income_section)
            if match:
                self.metrics['NetIncome'] = float(match.group(1).replace(',', ''))

            eps_diluted_pattern = r'Diluted.*?\$\s*([\d.]+)'
            match = re.search(eps_diluted_pattern, income_section)
            if match:
                self.metrics['EPS'] = float(match.group(1))

            # Cost of revenue for gross profit calculation
            cost_revenue_pattern = r'Cost of revenue.*?\$\s*([\d,]+)'
            match = re.search(cost_revenue_pattern, income_section)
            if match and 'Revenue' in self.metrics:
                cost_of_revenue = float(match.group(1).replace(',', ''))
                self.metrics['GrossProfit'] = self.metrics['Revenue'] - cost_of_revenue
                self.metrics['GrossMargin'] = round((self.metrics['GrossProfit'] / self.metrics['Revenue']) * 100, 1)

    def _extract_from_balance_sheet(self):
        """Extract balance sheet metrics."""
        # Find balance sheet section
        balance_pattern = r'CONDENSED CONSOLIDATED BALANCE SHEET.*?(?=CONDENSED CONSOLIDATED STATEMENTS|Table of Contents)'
        balance_match = re.search(balance_pattern, self.content, re.DOTALL | re.IGNORECASE)

        if not balance_match:
            balance_pattern = r'CONSOLIDATED BALANCE SHEET.*?(?=CONSOLIDATED STATEMENTS|Table of Contents)'
            balance_match = re.search(balance_pattern, self.content, re.DOTALL | re.IGNORECASE)

        if balance_match:
            balance_section = balance_match.group(0)

            # Cash and marketable securities
            cash_pattern = r'Cash and cash equivalents.*?\$\s*([\d,]+)'
            match = re.search(cash_pattern, balance_section)
            if match:
                cash = float(match.group(1).replace(',', ''))

                # Also get marketable securities
                securities_pattern = r'Marketable securities.*?\$\s*([\d,]+)'
                sec_match = re.search(securities_pattern, balance_section)
                if sec_match:
                    securities = float(sec_match.group(1).replace(',', ''))
                    self.metrics['CashAndEquivalents'] = cash + securities

            # Long-term debt
            debt_pattern = r'Long-term debt.*?\$\s*([\d,]+)'
            match = re.search(debt_pattern, balance_section)
            if match:
                self.metrics['TotalDebt'] = float(match.group(1).replace(',', ''))

            # Stockholders' equity
            equity_pattern = r'Total stockholders[\''] equity.*?\$\s*([\d,]+)'
            match = re.search(equity_pattern, balance_section)
            if match:
                self.metrics['ShareholdersEquity'] = float(match.group(1).replace(',', ''))

    def _extract_from_cash_flow(self):
        """Extract cash flow metrics."""
        cashflow_pattern = r'CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOW.*?(?=CONDENSED CONSOLIDATED|Table of Contents|See Accompanying)'
        cashflow_match = re.search(cashflow_pattern, self.content, re.DOTALL | re.IGNORECASE)

        if not cashflow_match:
            cashflow_pattern = r'CONSOLIDATED STATEMENTS OF CASH FLOW.*?(?=CONSOLIDATED STATEMENTS|Table of Contents|See Accompanying)'
            cashflow_match = re.search(cashflow_pattern, self.content, re.DOTALL | re.IGNORECASE)

        if cashflow_match:
            cashflow_section = cashflow_match.group(0)

            # Operating cash flow
            ocf_pattern = r'Net cash provided by operating activities.*?\$\s*([\d,]+)'
            match = re.search(ocf_pattern, cashflow_section)
            if match:
                self.metrics['OperatingCashFlow'] = float(match.group(1).replace(',', ''))

            # CapEx
            capex_pattern = r'Purchases of property and equipment.*?\$\s*\(?([\d,]+)\)?'
            match = re.search(capex_pattern, cashflow_section)
            if match:
                self.metrics['CapEx'] = float(match.group(1).replace(',', ''))

                # Calculate Free Cash Flow
                if 'OperatingCashFlow' in self.metrics:
                    self.metrics['FreeCashFlow'] = self.metrics['OperatingCashFlow'] - self.metrics['CapEx']

    def _extract_segment_data(self):
        """Extract segment reporting data (Family of Apps vs Reality Labs)."""
        # Look for segment results table
        segment_pattern = r'Segment Results.*?(?:Family of Apps|FoA).*?Reality Labs.*?Revenue.*?\$\s*([\d,]+).*?\$\s*([\d,]+).*?\$\s*([\d,]+)'
        match = re.search(segment_pattern, self.content, re.DOTALL | re.IGNORECASE)

        if match:
            # Typically: FoA Revenue, RL Revenue, Total Revenue
            foa_revenue = float(match.group(1).replace(',', ''))
            rl_revenue = float(match.group(2).replace(',', ''))

            # Ad revenue is approximately FoA revenue (most of it is ads)
            self.metrics['AdRevenue'] = foa_revenue
            self.metrics['RealityLabsRevenue'] = rl_revenue

            # Look for operating income/loss
            segment_income_pattern = r'Income \(loss\) from operations.*?\$\s*([\d,]+).*?\$\s*\(?([\d,]+)\)?'
            income_match = re.search(segment_income_pattern, self.content, re.DOTALL | re.IGNORECASE)
            if income_match:
                self.metrics['FamilyOfAppsIncome'] = float(income_match.group(1).replace(',', ''))
                rl_loss = float(income_match.group(2).replace(',', ''))
                self.metrics['RealityLabsLoss'] = rl_loss

    def _extract_user_metrics(self):
        """Extract user metrics (DAP, MAP, DAU, MAU, ARPP)."""
        # Family DAP (in billions, convert to millions)
        dap_pattern = r'Family daily active people.*?(?:DAP).*?was\s*([\d.]+)\s*billion'
        match = re.search(dap_pattern, self.content, re.IGNORECASE)
        if match:
            self.metrics['FamilyDAP'] = float(match.group(1)) * 1000  # Convert to millions

        # Facebook DAU
        fb_dau_pattern = r'(?:Facebook|FB).*?daily active users.*?(?:DAU).*?was\s*([\d.]+)\s*(?:billion|million)'
        match = re.search(fb_dau_pattern, self.content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            # Check if it's in billions or millions
            if 'billion' in match.group(0).lower():
                self.metrics['FacebookDAU'] = value * 1000
            else:
                self.metrics['FacebookDAU'] = value

        # Facebook MAU
        fb_mau_pattern = r'(?:Facebook|FB).*?monthly active users.*?(?:MAU).*?was\s*([\d.]+)\s*(?:billion|million)'
        match = re.search(fb_mau_pattern, self.content, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if 'billion' in match.group(0).lower():
                self.metrics['FacebookMAU'] = value * 1000
            else:
                self.metrics['FacebookMAU'] = value

        # ARPP (Average Revenue Per Person)
        arpp_pattern = r'average revenue per person.*?(?:ARPP).*?was\s*\$\s*([\d.]+)'
        match = re.search(arpp_pattern, self.content, re.IGNORECASE)
        if match:
            self.metrics['ARPP'] = float(match.group(1))

    def _extract_shares_outstanding(self):
        """Extract diluted shares outstanding."""
        # Look in the income statement for weighted average shares
        shares_pattern = r'Weighted-?average shares.*?(?:diluted|Diluted).*?\n.*?([\d,]+)'
        match = re.search(shares_pattern, self.content, re.IGNORECASE | re.MULTILINE)
        if match:
            self.metrics['SharesOutstanding'] = float(match.group(1).replace(',', ''))

    def extract_all(self) -> Dict:
        """Extract all metrics from the filing."""
        print(f"Processing: {self.file_path.name} -> {self.metrics['Period']}")

        self._extract_from_income_statement()
        self._extract_from_balance_sheet()
        self._extract_from_cash_flow()
        self._extract_segment_data()
        self._extract_user_metrics()
        self._extract_shares_outstanding()

        # Calculate operating margin if we have the data
        if 'OperatingIncome' in self.metrics and 'Revenue' in self.metrics and self.metrics['Revenue'] > 0:
            self.metrics['OperatingMargin'] = round((self.metrics['OperatingIncome'] / self.metrics['Revenue']) * 100, 1)

        # Calculate net margin if we have the data
        if 'NetIncome' in self.metrics and 'Revenue' in self.metrics and self.metrics['Revenue'] > 0:
            self.metrics['NetMargin'] = round((self.metrics['NetIncome'] / self.metrics['Revenue']) * 100, 1)

        return self.metrics


def parse_all_files():
    """Parse all extracted files and compile metrics."""
    all_metrics = []

    # Get all 10-K and 10-Q files
    files = sorted(EXTRACTED_DIR.glob("META_10*.txt"))

    for file_path in files:
        extractor = MetricsExtractor(file_path)
        metrics = extractor.extract_all()
        all_metrics.append(metrics)

    return all_metrics


def sort_periods(metrics_list: List[Dict]) -> List[Dict]:
    """Sort metrics by period chronologically."""
    def period_sort_key(metrics):
        period = metrics['Period']

        if period.startswith('FY'):
            # Annual: FY2014 -> (2014, 4, 0) for sorting after Q4
            year = int(period[2:])
            return (year, 4, 1)
        elif period.startswith('Q'):
            # Quarterly: Q1 2015 -> (2015, 1, 0)
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter, 0)
        else:
            return (0, 0, 0)

    return sorted(metrics_list, key=period_sort_key)


def write_csv(metrics_list: List[Dict]):
    """Write metrics to CSV file."""
    # Define all possible columns
    columns = [
        'Period', 'Revenue', 'GrossProfit', 'GrossMargin', 'OperatingIncome',
        'OperatingMargin', 'NetIncome', 'NetMargin', 'EPS', 'FreeCashFlow',
        'FamilyDAP', 'FamilyMAPE', 'FacebookDAU', 'FacebookMAU', 'ARPP',
        'AdRevenue', 'RealityLabsRevenue', 'RealityLabsLoss', 'FamilyOfAppsIncome',
        'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents',
        'SharesOutstanding', 'CapEx', 'OperatingCashFlow'
    ]

    # Sort metrics chronologically
    sorted_metrics = sort_periods(metrics_list)

    # Write CSV
    with open(OUTPUT_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for metrics in sorted_metrics:
            # Fill in empty values for missing columns
            row = {col: metrics.get(col, '') for col in columns}
            writer.writerow(row)

    print(f"\n✓ Wrote {len(sorted_metrics)} periods to {OUTPUT_CSV}")


if __name__ == "__main__":
    print("Meta Platforms Financial Metrics Extraction")
    print("=" * 60)

    metrics = parse_all_files()
    write_csv(metrics)

    print("\nExtraction complete!")
