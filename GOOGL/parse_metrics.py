#!/usr/bin/env python3
"""
Parse GOOGL financial metrics from extracted SEC filings
"""
import re
import csv
from pathlib import Path
from typing import Dict, Optional, List
import os

class GoogleMetricsParser:
    def __init__(self, extracted_dir: str):
        self.extracted_dir = Path(extracted_dir)
        self.metrics = []

    def clean_number(self, text: str) -> Optional[float]:
        """Extract numeric value from text, handling millions/thousands"""
        if not text:
            return None
        # Remove $ and commas
        text = text.replace('$', '').replace(',', '').strip()
        # Handle parentheses as negative
        if '(' in text and ')' in text:
            text = '-' + text.replace('(', '').replace(')', '')
        try:
            return float(text)
        except:
            return None

    def find_table_value(self, content: str, label: str, context_window: int = 500) -> Optional[float]:
        """Find a value in a table by searching for the label"""
        # Try to find the label
        pattern = re.compile(re.escape(label), re.IGNORECASE)
        match = pattern.search(content)
        if not match:
            return None

        # Extract text around the match
        start = max(0, match.start() - context_window)
        end = min(len(content), match.end() + context_window)
        context = content[start:end]

        # Look for numbers after the label
        # Pattern for currency amounts: optional $, digits with optional commas, optional decimals
        num_pattern = r'\$?\s*\(?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*\)?'
        numbers = re.findall(num_pattern, context[match.end()-start:])

        if numbers:
            return self.clean_number(numbers[0])
        return None

    def extract_period_from_filename(self, filename: str) -> str:
        """Extract period from filename like GOOGL_10Q_Q1-2020.txt -> Q1 2020"""
        if 'FY' in filename:
            match = re.search(r'FY(\d{4})', filename)
            if match:
                return f"FY{match.group(1)}"
        else:
            match = re.search(r'(Q[1-4])-(\d{4})', filename)
            if match:
                return f"{match.group(1)} {match.group(2)}"
        return ""

    def find_segment_revenues(self, content: str) -> Dict[str, Optional[float]]:
        """Extract Google's segment revenues"""
        segments = {
            'GoogleSearchRevenue': None,
            'YouTubeAdsRevenue': None,
            'GoogleCloudRevenue': None,
            'GoogleNetworkRevenue': None,
            'OtherBetsRevenue': None
        }

        # Search patterns for different revenue segments
        # Google Search & other
        search_patterns = [
            r'Google Search\s+&\s+other[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Search\s+&\s+other[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Google\s+Search[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in search_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                segments['GoogleSearchRevenue'] = self.clean_number(match.group(1))
                break

        # YouTube ads
        youtube_patterns = [
            r'YouTube\s+ads[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'YouTube\s+advertising[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in youtube_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                segments['YouTubeAdsRevenue'] = self.clean_number(match.group(1))
                break

        # Google Cloud
        cloud_patterns = [
            r'Google\s+Cloud[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Cloud[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in cloud_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                segments['GoogleCloudRevenue'] = self.clean_number(match.group(1))
                break

        # Google Network
        network_patterns = [
            r'Google\s+Network[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Network[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in network_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                segments['GoogleNetworkRevenue'] = self.clean_number(match.group(1))
                break

        # Other Bets
        other_patterns = [
            r'Other\s+Bets[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in other_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                segments['OtherBetsRevenue'] = self.clean_number(match.group(1))
                break

        return segments

    def parse_income_statement(self, content: str) -> Dict[str, Optional[float]]:
        """Extract income statement metrics"""
        metrics = {}

        # Revenue patterns - look for "Total revenues"
        revenue_patterns = [
            r'Total\s+revenues[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Revenues[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in revenue_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                # Take the first occurrence (current period)
                metrics['Revenue'] = self.clean_number(matches[0])
                break

        # Cost of revenues
        cost_patterns = [
            r'Cost\s+of\s+revenues[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Total\s+cost\s+of\s+revenues[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        cost_of_revenue = None
        for pattern in cost_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                cost_of_revenue = self.clean_number(matches[0])
                break

        # Calculate gross profit if we have revenue and cost
        if metrics.get('Revenue') and cost_of_revenue:
            metrics['GrossProfit'] = metrics['Revenue'] - cost_of_revenue
            metrics['GrossMargin'] = round((metrics['GrossProfit'] / metrics['Revenue']) * 100, 1)

        # Operating income
        op_income_patterns = [
            r'Income\s+from\s+operations[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Operating\s+income[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in op_income_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                metrics['OperatingIncome'] = self.clean_number(matches[0])
                if metrics.get('Revenue'):
                    metrics['OperatingMargin'] = round((metrics['OperatingIncome'] / metrics['Revenue']) * 100, 1)
                break

        # Net income
        net_income_patterns = [
            r'Net\s+income[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Net\s+income\s+\(loss\)[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in net_income_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                metrics['NetIncome'] = self.clean_number(matches[0])
                if metrics.get('Revenue'):
                    metrics['NetMargin'] = round((metrics['NetIncome'] / metrics['Revenue']) * 100, 1)
                break

        # EPS (diluted)
        eps_patterns = [
            r'Diluted\s+net\s+income\s+per\s+share[^\d]*\$?\s*(\d+\.\d+)',
            r'Diluted[^\d]*\$?\s*(\d+\.\d+)',
            r'Basic\s+and\s+diluted\s+net\s+income\s+per\s+share[^\d]*\$?\s*(\d+\.\d+)'
        ]

        for pattern in eps_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                metrics['EPS'] = self.clean_number(matches[0])
                break

        return metrics

    def parse_balance_sheet(self, content: str) -> Dict[str, Optional[float]]:
        """Extract balance sheet metrics"""
        metrics = {}

        # Total assets
        asset_patterns = [
            r'Total\s+assets[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
        ]

        for pattern in asset_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                # Often appears multiple times, take last occurrence for most recent
                metrics['TotalAssets'] = self.clean_number(matches[-1])
                break

        # Cash and equivalents
        cash_patterns = [
            r'Cash\s+and\s+cash\s+equivalents[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Total\s+cash.*equivalents.*securities[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in cash_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                metrics['CashAndEquivalents'] = self.clean_number(matches[-1])
                break

        # Total debt
        debt_patterns = [
            r'Total\s+debt[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Long-term\s+debt[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in debt_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                metrics['TotalDebt'] = self.clean_number(matches[-1])
                break

        # Shareholders' equity
        equity_patterns = [
            r'Total\s+stockholders.*equity[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Total\s+shareholders.*equity[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Stockholders.*equity[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in equity_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                metrics['ShareholdersEquity'] = self.clean_number(matches[-1])
                break

        return metrics

    def parse_cash_flow(self, content: str) -> Dict[str, Optional[float]]:
        """Extract cash flow metrics"""
        metrics = {}

        # Operating cash flow
        ocf_patterns = [
            r'Net\s+cash\s+provided\s+by\s+operating\s+activities[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'Cash\s+flows?\s+from\s+operating\s+activities[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        operating_cf = None
        for pattern in ocf_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                operating_cf = self.clean_number(matches[0])
                break

        # Capital expenditures
        capex_patterns = [
            r'Purchases\s+of\s+property\s+and\s+equipment[^\d]*\$?\s*\(?\s*(\d{1,3}(?:,\d{3})*)\s*\)?',
            r'Capital\s+expenditures[^\d]*\$?\s*\(?\s*(\d{1,3}(?:,\d{3})*)\s*\)?'
        ]

        capex = None
        for pattern in capex_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                capex = self.clean_number(matches[0])
                # CapEx is usually negative or in parentheses
                if capex and capex > 0:
                    capex = -capex
                break

        # Calculate free cash flow
        if operating_cf and capex:
            metrics['FreeCashFlow'] = operating_cf + capex  # capex is negative

        return metrics

    def parse_other_metrics(self, content: str) -> Dict[str, Optional[float]]:
        """Extract other important metrics"""
        metrics = {}

        # Number of employees
        employee_patterns = [
            r'(\d{1,3}(?:,\d{3})*)\s+employees',
            r'employees.*?(\d{1,3}(?:,\d{3})*)',
            r'headcount.*?(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in employee_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                metrics['NumberOfEmployees'] = self.clean_number(matches[-1])
                break

        # Shares outstanding (diluted)
        shares_patterns = [
            r'Diluted.*?shares.*?(\d{1,3}(?:,\d{3})*)',
            r'Number\s+of\s+shares.*diluted.*?(\d{1,3}(?:,\d{3})*)',
            r'Weighted-average.*diluted.*?(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in shares_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                # Shares are typically in millions already
                metrics['SharesOutstanding'] = self.clean_number(matches[0])
                break

        # Traffic acquisition costs
        tac_patterns = [
            r'Traffic\s+acquisition\s+costs[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)',
            r'TAC[^\d]*\$?\s*(\d{1,3}(?:,\d{3})*)'
        ]

        for pattern in tac_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                metrics['TrafficAcquisitionCosts'] = self.clean_number(matches[0])
                break

        return metrics

    def parse_file(self, filepath: Path) -> Optional[Dict]:
        """Parse a single filing"""
        period = self.extract_period_from_filename(filepath.name)
        if not period:
            print(f"Could not extract period from {filepath.name}")
            return None

        print(f"Parsing {filepath.name} for {period}...")

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading {filepath}: {e}")
            return None

        # Extract all metrics
        metrics = {'Period': period}

        # Income statement
        income_metrics = self.parse_income_statement(content)
        metrics.update(income_metrics)

        # Balance sheet
        balance_metrics = self.parse_balance_sheet(content)
        metrics.update(balance_metrics)

        # Cash flow
        cf_metrics = self.parse_cash_flow(content)
        metrics.update(cf_metrics)

        # Segment revenues
        segment_metrics = self.find_segment_revenues(content)
        metrics.update(segment_metrics)

        # Other metrics
        other_metrics = self.parse_other_metrics(content)
        metrics.update(other_metrics)

        return metrics

    def parse_all_files(self):
        """Parse all extracted files"""
        # Get all txt files
        files = sorted(self.extracted_dir.glob('*.txt'))

        for filepath in files:
            metrics = self.parse_file(filepath)
            if metrics:
                self.metrics.append(metrics)

        # Sort by period
        self.metrics.sort(key=lambda x: self.sort_key(x['Period']))

    def sort_key(self, period: str) -> tuple:
        """Generate sort key for period"""
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 4, 0)  # FY goes at end of year
        elif period.startswith('Q'):
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter, 0)
        return (0, 0, 0)

    def write_csv(self, output_path: str):
        """Write metrics to CSV"""
        if not self.metrics:
            print("No metrics to write")
            return

        # Define field order
        fieldnames = [
            'Period', 'Revenue', 'GrossProfit', 'GrossMargin',
            'OperatingIncome', 'OperatingMargin', 'NetIncome', 'EPS',
            'FreeCashFlow', 'ShareholdersEquity', 'TotalDebt',
            'CashAndEquivalents', 'SharesOutstanding',
            'GoogleSearchRevenue', 'YouTubeAdsRevenue', 'GoogleCloudRevenue',
            'GoogleNetworkRevenue', 'OtherBetsRevenue', 'TrafficAcquisitionCosts',
            'NumberOfEmployees'
        ]

        # Ensure output directory exists
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()

            for metric in self.metrics:
                # Fill in missing fields with empty string
                row = {field: metric.get(field, '') for field in fieldnames}
                # Format numbers to 1 decimal place where appropriate
                for field in fieldnames:
                    if field not in ['Period'] and row[field] != '':
                        try:
                            val = float(row[field])
                            if field in ['GrossMargin', 'OperatingMargin', 'NetMargin']:
                                row[field] = f"{val:.1f}"
                            elif field == 'EPS':
                                row[field] = f"{val:.2f}"
                            else:
                                row[field] = f"{val:.0f}" if val == int(val) else f"{val:.1f}"
                        except:
                            pass
                writer.writerow(row)

        print(f"\nWrote {len(self.metrics)} periods to {output_path}")


def main():
    extracted_dir = "/Users/swilliams/Stocks/Research/GOOGL/Extracted"
    output_csv = "/Users/swilliams/Stocks/Research/GOOGL/Reports/GOOGL_Metrics.csv"

    parser = GoogleMetricsParser(extracted_dir)
    parser.parse_all_files()
    parser.write_csv(output_csv)

    # Display first and last 10 rows
    if parser.metrics:
        print("\n" + "="*80)
        print("FIRST 10 PERIODS:")
        print("="*80)
        for i, metric in enumerate(parser.metrics[:10]):
            print(f"\n{metric['Period']}:")
            for key, value in metric.items():
                if key != 'Period' and value is not None:
                    print(f"  {key}: {value}")

        print("\n" + "="*80)
        print("LAST 10 PERIODS:")
        print("="*80)
        for i, metric in enumerate(parser.metrics[-10:]):
            print(f"\n{metric['Period']}:")
            for key, value in metric.items():
                if key != 'Period' and value is not None:
                    print(f"  {key}: {value}")


if __name__ == '__main__':
    main()
