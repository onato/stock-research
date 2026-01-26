#!/usr/bin/env python3
"""
Robust GOOGL financial metrics extractor
Handles PDF-extracted text with various formatting issues
"""

import re
import csv
from pathlib import Path
from typing import Dict, Optional, List, Tuple
import sys

class AlphabetMetricsExtractor:
    """Extract financial metrics from Alphabet/Google SEC filings"""

    def __init__(self, extracted_dir: str):
        self.extracted_dir = Path(extracted_dir)
        self.all_metrics = []

    def clean_number(self, text: str) -> Optional[float]:
        """Convert text to number, handling various formats"""
        if not text or text.strip() == '':
            return None

        text = str(text).strip()
        # Remove $, spaces, and handle parentheses as negative
        text = text.replace('$', '').replace(' ', '').replace(',', '')

        # Handle parentheses as negative numbers
        if '(' in text or ')' in text:
            text = text.replace('(', '-').replace(')', '')

        try:
            return float(text)
        except (ValueError, AttributeError):
            return None

    def extract_period(self, filename: str) -> str:
        """Extract period identifier from filename"""
        # FY2024 from GOOGL_10K_FY2024.txt
        fy_match = re.search(r'FY(\d{4})', filename)
        if fy_match:
            return f"FY{fy_match.group(1)}"

        # Q1 2024 from GOOGL_10Q_Q1-2024.txt
        q_match = re.search(r'(Q[1-4])-(\d{4})', filename)
        if q_match:
            return f"{q_match.group(1)} {q_match.group(2)}"

        return ""

    def find_financial_tables(self, content: str, period_marker: str) -> Dict[str, str]:
        """
        Find financial statement sections
        period_marker examples: "March 31, 2020", "December 31, 2020"
        """
        sections = {}

        # Find consolidated statements of income section
        income_patterns = [
            r'ALPHABET\s+INC\..*?CONSOLIDATED\s+STATEMENTS?\s+OF\s+INCOME',
            r'Consolidated\s+Statements?\s+of\s+Income',
            r'CONSOLIDATED\s+STATEMENTS?\s+OF\s+INCOME'
        ]

        for pattern in income_patterns:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                # Extract the next 5000 characters as the income statement
                start = match.start()
                sections['income'] = content[start:start+5000]
                break

        # Find balance sheet section
        balance_patterns = [
            r'ALPHABET\s+INC\..*?CONSOLIDATED\s+BALANCE\s+SHEETS?',
            r'Consolidated\s+Balance\s+Sheets?',
            r'CONSOLIDATED\s+BALANCE\s+SHEETS?'
        ]

        for pattern in balance_patterns:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                start = match.start()
                sections['balance'] = content[start:start+5000]
                break

        # Find cash flow section
        cf_patterns = [
            r'ALPHABET\s+INC\..*?CONSOLIDATED\s+STATEMENTS?\s+OF\s+CASH\s+FLOWS?',
            r'Consolidated\s+Statements?\s+of\s+Cash\s+Flows?',
            r'CONSOLIDATED\s+STATEMENTS?\s+OF\s+CASH\s+FLOWS?'
        ]

        for pattern in cf_patterns:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                start = match.start()
                sections['cashflow'] = content[start:start+5000]
                break

        return sections

    def extract_from_table_line(self, text: str, label: str, take_first: bool = True) -> Optional[float]:
        """
        Extract number from a line that starts with label
        Example: "Total revenues $ 41,159 $ 36,339" -> extract 41,159
        """
        # Escape special regex characters in label
        label_escaped = re.escape(label)

        # Look for the label followed by numbers
        # Pattern: label, optional whitespace, $, number(s)
        pattern = rf'{label_escaped}.*?\$\s*([\d,]+(?:\.\d+)?)'

        matches = re.findall(pattern, text, re.IGNORECASE)

        if matches:
            if take_first:
                return self.clean_number(matches[0])
            else:
                return self.clean_number(matches[-1])

        return None

    def extract_revenues_by_segment(self, content: str) -> Dict[str, Optional[float]]:
        """Extract segment revenues - Alphabet reports these in tables"""
        segments = {}

        # Look for segment revenue table
        # Pattern to find "Revenues:" section with breakdown
        segment_section_pattern = r'Revenues:.*?(?=Total\s+revenues|Costs\s+and\s+expenses)'

        segment_match = re.search(segment_section_pattern, content, re.IGNORECASE | re.DOTALL)

        if segment_match:
            segment_text = segment_match.group()

            # Google Search & other
            search_val = self.extract_from_table_line(segment_text, 'Google Search & other')
            if not search_val:
                search_val = self.extract_from_table_line(segment_text, 'Search & other')
            segments['GoogleSearchRevenue'] = search_val

            # YouTube ads
            youtube_val = self.extract_from_table_line(segment_text, 'YouTube ads')
            segments['YouTubeAdsRevenue'] = youtube_val

            # Google Network
            network_val = self.extract_from_table_line(segment_text, 'Google Network')
            if not network_val:
                network_val = self.extract_from_table_line(segment_text, 'Network')
            segments['GoogleNetworkRevenue'] = network_val

            # Google Cloud
            cloud_val = self.extract_from_table_line(segment_text, 'Google Cloud')
            segments['GoogleCloudRevenue'] = cloud_val

            # Other Bets
            other_val = self.extract_from_table_line(segment_text, 'Other Bets')
            segments['OtherBetsRevenue'] = other_val

        return segments

    def extract_income_statement(self, content: str) -> Dict[str, Optional[float]]:
        """Extract income statement metrics"""
        metrics = {}

        # Total revenues (first occurrence is current period)
        metrics['Revenue'] = self.extract_from_table_line(content, 'Total revenues')

        # Cost of revenues
        cost_of_rev = self.extract_from_table_line(content, 'Cost of revenues')
        if not cost_of_rev:
            cost_of_rev = self.extract_from_table_line(content, 'Total cost of revenues')

        # Calculate gross profit and margin
        if metrics.get('Revenue') and cost_of_rev:
            metrics['GrossProfit'] = metrics['Revenue'] - cost_of_rev
            metrics['GrossMargin'] = round((metrics['GrossProfit'] / metrics['Revenue']) * 100, 1)

        # Operating income
        op_income = self.extract_from_table_line(content, 'Income from operations')
        if not op_income:
            op_income = self.extract_from_table_line(content, 'Operating income')
        metrics['OperatingIncome'] = op_income

        if metrics.get('Revenue') and op_income:
            metrics['OperatingMargin'] = round((op_income / metrics['Revenue']) * 100, 1)

        # Net income
        net_income = self.extract_from_table_line(content, 'Net income')
        metrics['NetIncome'] = net_income

        if metrics.get('Revenue') and net_income:
            metrics['NetMargin'] = round((net_income / metrics['Revenue']) * 100, 1)

        # EPS diluted - look for earnings per share section
        eps_pattern = r'(?:Diluted|Basic and diluted).*?earnings\s+per\s+share.*?\$\s*([\d.]+)'
        eps_match = re.search(eps_pattern, content, re.IGNORECASE | re.DOTALL)
        if eps_match:
            metrics['EPS'] = self.clean_number(eps_match.group(1))

        # Shares outstanding (diluted) - in millions
        shares_pattern = r'(?:Diluted|Number of shares used).*?(\d{1,3}(?:,\d{3})*)'
        shares_matches = re.findall(shares_pattern, content, re.IGNORECASE)
        if shares_matches:
            metrics['SharesOutstanding'] = self.clean_number(shares_matches[0])

        return metrics

    def extract_balance_sheet(self, content: str) -> Dict[str, Optional[float]]:
        """Extract balance sheet metrics - take most recent values"""
        metrics = {}

        # Cash and cash equivalents
        cash_val = self.extract_from_table_line(content, 'Cash and cash equivalents')
        if cash_val:
            metrics['CashAndEquivalents'] = cash_val

        # Could also include marketable securities
        marketable_val = self.extract_from_table_line(content, 'Marketable securities')
        if marketable_val and cash_val:
            # Total liquid assets
            metrics['CashAndEquivalents'] = cash_val + marketable_val

        # Total assets
        metrics['TotalAssets'] = self.extract_from_table_line(content, 'Total assets', take_first=False)

        # Total debt (may need to sum short-term and long-term)
        long_term_debt = self.extract_from_table_line(content, 'Long-term debt')
        short_term_debt = self.extract_from_table_line(content, 'Current portion of long-term debt')

        if long_term_debt:
            total_debt = long_term_debt
            if short_term_debt:
                total_debt += short_term_debt
            metrics['TotalDebt'] = total_debt

        # Stockholders' equity
        equity_val = self.extract_from_table_line(content, "Total stockholders' equity", take_first=False)
        if not equity_val:
            equity_val = self.extract_from_table_line(content, 'Total equity', take_first=False)
        metrics['ShareholdersEquity'] = equity_val

        return metrics

    def extract_cashflow(self, content: str) -> Dict[str, Optional[float]]:
        """Extract cash flow metrics"""
        metrics = {}

        # Net cash from operating activities
        ocf = self.extract_from_table_line(content, 'Net cash provided by operating activities')
        if not ocf:
            ocf = self.extract_from_table_line(content, 'Net cash from operating activities')

        # Purchases of property and equipment (CapEx)
        capex = self.extract_from_table_line(content, 'Purchases of property and equipment')

        # Calculate FCF if we have both
        if ocf and capex:
            # CapEx should be negative, but let's ensure it
            if capex > 0:
                capex = -capex
            metrics['FreeCashFlow'] = ocf + capex

        return metrics

    def extract_other_metrics(self, content: str) -> Dict[str, Optional[float]]:
        """Extract additional metrics like TAC, employees"""
        metrics = {}

        # Traffic Acquisition Costs
        tac = self.extract_from_table_line(content, 'Traffic acquisition costs')
        if tac:
            metrics['TrafficAcquisitionCosts'] = tac

        # Number of employees - usually mentioned in text
        emp_patterns = [
            r'(\d{1,3},\d{3})\s+(?:full-time\s+)?employees',
            r'employees.*?(\d{1,3},\d{3})',
            r'headcount.*?(\d{1,3},\d{3})'
        ]

        for pattern in emp_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                metrics['NumberOfEmployees'] = self.clean_number(match.group(1))
                break

        return metrics

    def process_file(self, filepath: Path) -> Optional[Dict]:
        """Process a single SEC filing"""
        period = self.extract_period(filepath.name)
        if not period:
            return None

        print(f"Processing {filepath.name} -> {period}")

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            print(f"  ERROR reading file: {e}")
            return None

        # Initialize metrics for this period
        metrics = {'Period': period}

        # Find financial statement sections
        sections = self.find_financial_tables(content, period)

        # Extract from income statement
        if 'income' in sections:
            income_metrics = self.extract_income_statement(sections['income'])
            metrics.update(income_metrics)

            # Also extract segment revenues
            segment_metrics = self.extract_revenues_by_segment(sections['income'])
            metrics.update(segment_metrics)
        else:
            # Try full content if section not found
            income_metrics = self.extract_income_statement(content[:20000])
            metrics.update(income_metrics)
            segment_metrics = self.extract_revenues_by_segment(content[:20000])
            metrics.update(segment_metrics)

        # Extract from balance sheet
        if 'balance' in sections:
            balance_metrics = self.extract_balance_sheet(sections['balance'])
            metrics.update(balance_metrics)
        else:
            balance_metrics = self.extract_balance_sheet(content[:20000])
            metrics.update(balance_metrics)

        # Extract from cash flow
        if 'cashflow' in sections:
            cf_metrics = self.extract_cashflow(sections['cashflow'])
            metrics.update(cf_metrics)
        else:
            cf_metrics = self.extract_cashflow(content[:20000])
            metrics.update(cf_metrics)

        # Extract other metrics
        other_metrics = self.extract_other_metrics(content[:30000])
        metrics.update(other_metrics)

        # Print summary of what was found
        found_fields = [k for k, v in metrics.items() if v is not None and k != 'Period']
        print(f"  Found {len(found_fields)} metrics: {', '.join(found_fields[:5])}...")

        return metrics

    def get_sort_key(self, period: str) -> Tuple[int, int]:
        """Generate sort key for chronological ordering"""
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 5)  # FY comes after Q4
        elif period.startswith('Q'):
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter)
        return (0, 0)

    def process_all_files(self):
        """Process all extracted text files"""
        txt_files = sorted(self.extracted_dir.glob('GOOGL_*.txt'))

        print(f"Found {len(txt_files)} files to process\n")

        for filepath in txt_files:
            metrics = self.process_file(filepath)
            if metrics:
                self.all_metrics.append(metrics)

        # Sort chronologically
        self.all_metrics.sort(key=lambda x: self.get_sort_key(x['Period']))

        print(f"\n✓ Successfully processed {len(self.all_metrics)} periods")

    def write_csv(self, output_path: str):
        """Write all metrics to CSV"""
        if not self.all_metrics:
            print("No metrics to write!")
            return

        # Define column order
        columns = [
            'Period', 'Revenue', 'GrossProfit', 'GrossMargin',
            'OperatingIncome', 'OperatingMargin', 'NetIncome', 'NetMargin',
            'EPS', 'FreeCashFlow',
            'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding',
            'GoogleSearchRevenue', 'YouTubeAdsRevenue', 'GoogleCloudRevenue',
            'GoogleNetworkRevenue', 'OtherBetsRevenue',
            'TrafficAcquisitionCosts', 'NumberOfEmployees'
        ]

        # Ensure output directory exists
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Write CSV
        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()

            for metrics in self.all_metrics:
                # Format row
                row = {}
                for col in columns:
                    value = metrics.get(col)
                    if value is None or value == '':
                        row[col] = ''
                    elif col == 'Period':
                        row[col] = value
                    elif col == 'EPS':
                        row[col] = f"{float(value):.2f}"
                    elif col in ['GrossMargin', 'OperatingMargin', 'NetMargin']:
                        row[col] = f"{float(value):.1f}"
                    else:
                        # Round to whole number for dollar amounts
                        row[col] = f"{float(value):.0f}"

                writer.writerow(row)

        print(f"\n✓ Wrote CSV to: {output_path}")
        print(f"  {len(self.all_metrics)} periods × {len(columns)} columns")

    def display_summary(self):
        """Display first and last 10 periods"""
        if not self.all_metrics:
            return

        print("\n" + "=" * 80)
        print("FIRST 10 PERIODS")
        print("=" * 80)

        for metrics in self.all_metrics[:10]:
            period = metrics['Period']
            revenue = metrics.get('Revenue', 'N/A')
            net_income = metrics.get('NetIncome', 'N/A')
            eps = metrics.get('EPS', 'N/A')

            print(f"{period:12s} | Revenue: {revenue:>10} | Net Income: {net_income:>10} | EPS: {eps:>6}")

        print("\n" + "=" * 80)
        print("LAST 10 PERIODS")
        print("=" * 80)

        for metrics in self.all_metrics[-10:]:
            period = metrics['Period']
            revenue = metrics.get('Revenue', 'N/A')
            net_income = metrics.get('NetIncome', 'N/A')
            eps = metrics.get('EPS', 'N/A')

            print(f"{period:12s} | Revenue: {revenue:>10} | Net Income: {net_income:>10} | EPS: {eps:>6}")

        print("=" * 80)


def main():
    """Main execution"""
    extracted_dir = "/Users/swilliams/Stocks/Research/GOOGL/Extracted"
    output_csv = "/Users/swilliams/Stocks/Research/GOOGL/Reports/GOOGL_Metrics.csv"

    print("GOOGL Financial Metrics Extractor")
    print("=" * 80)
    print(f"Input:  {extracted_dir}")
    print(f"Output: {output_csv}")
    print("=" * 80 + "\n")

    extractor = AlphabetMetricsExtractor(extracted_dir)
    extractor.process_all_files()
    extractor.write_csv(output_csv)
    extractor.display_summary()

    print("\n✓ Complete!")


if __name__ == '__main__':
    main()
