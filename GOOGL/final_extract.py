#!/usr/bin/env python3
"""
GOOGL Financial Metrics Extractor - Final Version
Handles long-line PDF extractions from SEC filings
"""

import re
import csv
import json
from pathlib import Path
from typing import Dict, Optional, List

class GOOGLExtractor:
    """Extract financial metrics from GOOGL SEC filings"""

    def __init__(self):
        self.results = []

    @staticmethod
    def clean_num(text):
        """Convert text to float, handling $ ( ) ,"""
        if not text:
            return None
        text = str(text).replace('$', '').replace(',', '').strip()
        if '(' in text:
            text = '-' + text.replace('(', '').replace(')', '')
        try:
            return float(text)
        except:
            return None

    @staticmethod
    def extract_period(filename):
        """Get period from filename"""
        if 'FY' in filename:
            m = re.search(r'FY(\d{4})', filename)
            return f"FY{m.group(1)}" if m else None
        m = re.search(r'(Q[1-4])-(\d{4})', filename)
        return f"{m.group(1)} {m.group(2)}" if m else None

    def find_value_after_label(self, text, label, nth=0):
        """
        Find the nth number after a label in text
        nth=0 means first occurrence (current period)
        nth=1 means second occurrence (prior period)
        """
        # Create pattern: label followed by optional text, then dollar amount
        pattern = rf'{re.escape(label)}[^\d$]*\$?\s*(\d{{1,3}}(?:,\d{{3}})*(?:\.\d+)?)'

        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches and nth < len(matches):
            return self.clean_num(matches[nth])
        return None

    def extract_quarterly(self, filepath):
        """Extract metrics from a 10-Q filing"""
        period = self.extract_period(filepath.name)
        if not period:
            return None

        print(f"Extracting {period:12s} from {filepath.name}")

        try:
            # Read file - even if it has very long lines
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                # Read in chunks to handle large files
                content = f.read(100000)  # First 100K chars should have all tables
        except Exception as e:
            print(f"  Error reading: {e}")
            return None

        metrics = {'Period': period}

        # Revenue
        metrics['Revenue'] = self.find_value_after_label(content, 'Total revenues')

        # Cost of revenues
        cost_rev = self.find_value_after_label(content, 'Cost of revenues')
        if metrics.get('Revenue') and cost_rev:
            metrics['GrossProfit'] = metrics['Revenue'] - cost_rev
            metrics['GrossMargin'] = round(100 * metrics['GrossProfit'] / metrics['Revenue'], 1)

        # Operating income
        op_inc = self.find_value_after_label(content, 'Income from operations')
        if not op_inc:
            op_inc = self.find_value_after_label(content, 'Operating income')
        if op_inc:
            metrics['OperatingIncome'] = op_inc
            if metrics.get('Revenue'):
                metrics['OperatingMargin'] = round(100 * op_inc / metrics['Revenue'], 1)

        # Net income
        net_inc = self.find_value_after_label(content, 'Net income')
        if net_inc:
            metrics['NetIncome'] = net_inc
            if metrics.get('Revenue'):
                metrics['NetMargin'] = round(100 * net_inc / metrics['Revenue'], 1)

        # EPS - special pattern
        eps_pattern = r'(?:Basic and diluted|Diluted).*?(?:net income per share|earnings per share).*?\$\s*(\d+\.\d+)'
        eps_match = re.search(eps_pattern, content, re.IGNORECASE)
        if eps_match:
            metrics['EPS'] = self.clean_num(eps_match.group(1))

        # Shares outstanding
        shares_pattern = r'(?:Number of shares|Weighted-average shares).*?(?:diluted|outstanding).*?(\d{1,3}(?:,\d{3})*)'
        shares_match = re.search(shares_pattern, content, re.IGNORECASE)
        if shares_match:
            metrics['SharesOutstanding'] = self.clean_num(shares_match.group(1))

        # Segment revenues - Google breaks these out
        metrics['GoogleSearchRevenue'] = self.find_value_after_label(content, 'Google Search & other')
        if not metrics.get('GoogleSearchRevenue'):
            metrics['GoogleSearchRevenue'] = self.find_value_after_label(content, 'Search & other')

        metrics['YouTubeAdsRevenue'] = self.find_value_after_label(content, 'YouTube ads')
        metrics['GoogleCloudRevenue'] = self.find_value_after_label(content, 'Google Cloud')
        metrics['GoogleNetworkRevenue'] = self.find_value_after_label(content, 'Google Network')
        metrics['OtherBetsRevenue'] = self.find_value_after_label(content, 'Other Bets')

        # Balance sheet items (from most recent column)
        cash_pattern = r'Cash and cash equivalents.*?\$\s*(\d{1,3}(?:,\d{3})*)'
        cash_matches = re.findall(cash_pattern, content, re.IGNORECASE)
        if cash_matches:
            metrics['CashAndEquivalents'] = self.clean_num(cash_matches[-1])

        equity_pattern = r'Total stockholders.*equity.*?\$\s*(\d{1,3}(?:,\d{3})*)'
        equity_matches = re.findall(equity_pattern, content, re.IGNORECASE)
        if equity_matches:
            metrics['ShareholdersEquity'] = self.clean_num(equity_matches[-1])

        debt_pattern = r'(?:Long-term debt|Total debt).*?\$\s*(\d{1,3}(?:,\d{3})*)'
        debt_matches = re.findall(debt_pattern, content, re.IGNORECASE)
        if debt_matches:
            metrics['TotalDebt'] = self.clean_num(debt_matches[-1])

        # Cash flow
        ocf_pattern = r'Net cash provided by operating activities.*?\$\s*(\d{1,3}(?:,\d{3})*)'
        ocf_match = re.search(ocf_pattern, content, re.IGNORECASE)

        capex_pattern = r'Purchases of property and equipment.*?\$\s*\(?\s*(\d{1,3}(?:,\d{3})*)\s*\)?'
        capex_match = re.search(capex_pattern, content, re.IGNORECASE)

        if ocf_match and capex_match:
            ocf = self.clean_num(ocf_match.group(1))
            capex = self.clean_num(capex_match.group(1))
            if ocf and capex:
                metrics['FreeCashFlow'] = ocf - capex

        # Other metrics
        tac = self.find_value_after_label(content, 'Traffic acquisition costs')
        if tac:
            metrics['TrafficAcquisitionCosts'] = tac

        # Employees
        emp_pattern = r'(\d{1,3},\d{3})\s+(?:full-time\s+)?employees'
        emp_match = re.search(emp_pattern, content, re.IGNORECASE)
        if emp_match:
            metrics['NumberOfEmployees'] = self.clean_num(emp_match.group(1))

        # Count non-None metrics
        filled = sum(1 for k, v in metrics.items() if v is not None and k != 'Period')
        print(f"  → Found {filled} metrics")

        return metrics

    def process_all(self, extracted_dir):
        """Process all files in directory"""
        path = Path(extracted_dir)
        files = sorted(path.glob('GOOGL_*.txt'))

        print(f"Found {len(files)} files to process\n")
        print("-" * 70)

        for f in files:
            result = self.extract_quarterly(f)
            if result:
                self.results.append(result)

        # Sort chronologically
        self.results.sort(key=self._sort_key)

        print("-" * 70)
        print(f"\n✓ Extracted {len(self.results)} periods successfully\n")

    def _sort_key(self, item):
        """Sort key for chronological order"""
        period = item['Period']
        if period.startswith('FY'):
            return (int(period[2:]), 5)
        parts = period.split()
        return (int(parts[1]), int(parts[0][1]))

    def write_csv(self, output_path):
        """Write results to CSV"""
        if not self.results:
            print("No results to write")
            return

        columns = [
            'Period', 'Revenue', 'GrossProfit', 'GrossMargin',
            'OperatingIncome', 'OperatingMargin', 'NetIncome', 'NetMargin',
            'EPS', 'FreeCashFlow',
            'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding',
            'GoogleSearchRevenue', 'YouTubeAdsRevenue', 'GoogleCloudRevenue',
            'GoogleNetworkRevenue', 'OtherBetsRevenue',
            'TrafficAcquisitionCosts', 'NumberOfEmployees'
        ]

        # Create output directory
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()

            for result in self.results:
                row = {}
                for col in columns:
                    val = result.get(col)
                    if val is None:
                        row[col] = ''
                    elif col == 'Period':
                        row[col] = val
                    elif col == 'EPS':
                        row[col] = f"{float(val):.2f}"
                    elif 'Margin' in col:
                        row[col] = f"{float(val):.1f}"
                    else:
                        row[col] = f"{float(val):.0f}"
                writer.writerow(row)

        print(f"✓ Wrote CSV: {output_path}")
        print(f"  {len(self.results)} periods × {len(columns)} columns\n")

    def display_summary(self, n=10):
        """Show first and last n periods"""
        if not self.results:
            return

        print("=" * 80)
        print(f"FIRST {n} PERIODS")
        print("=" * 80)
        self._print_rows(self.results[:n])

        print("\n" + "=" * 80)
        print(f"LAST {n} PERIODS")
        print("=" * 80)
        self._print_rows(self.results[-n:])
        print("=" * 80)

    def _print_rows(self, rows):
        """Print formatted rows"""
        for row in rows:
            period = row['Period']
            rev = row.get('Revenue', '')
            ni = row.get('NetIncome', '')
            eps = row.get('EPS', '')
            fcf = row.get('FreeCashFlow', '')

            rev_str = f"${rev:>7,.0f}M" if rev else "N/A".rjust(11)
            ni_str = f"${ni:>7,.0f}M" if ni else "N/A".rjust(11)
            eps_str = f"${eps:>5.2f}" if eps else "N/A".rjust(7)
            fcf_str = f"${fcf:>7,.0f}M" if fcf else "N/A".rjust(11)

            print(f"  {period:12s}  Rev: {rev_str}  NI: {ni_str}  EPS: {eps_str}  FCF: {fcf_str}")


def main():
    """Main entry point"""
    print("\n" + "=" * 80)
    print(" GOOGL Financial Metrics Extractor".center(80))
    print("=" * 80 + "\n")

    extracted_dir = "/Users/swilliams/Stocks/Research/GOOGL/Extracted"
    output_csv = "/Users/swilliams/Stocks/Research/GOOGL/Reports/GOOGL_Metrics.csv"

    print(f"Input directory:  {extracted_dir}")
    print(f"Output CSV:       {output_csv}\n")

    extractor = GOOGLExtractor()
    extractor.process_all(extracted_dir)
    extractor.write_csv(output_csv)
    extractor.display_summary(10)

    print("\n✓ Extraction complete!\n")


if __name__ == '__main__':
    main()
