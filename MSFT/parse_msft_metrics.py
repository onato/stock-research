#!/usr/bin/env python3
"""
Extract Microsoft financial metrics from SEC filings (iXBRL format).
Handles both 10-K (annual) and 10-Q (quarterly) reports.
"""

import re
import csv
from pathlib import Path
from typing import Dict, Optional, List
import sys

# Base directory
BASE_DIR = Path("/Users/swilliams/Stocks/Research/MSFT")
EXTRACTED_DIR = BASE_DIR / "Extracted"
REPORTS_DIR = BASE_DIR / "Reports"


class MSFTMetricsParser:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.content = file_path.read_text()
        self.lines = [line.strip() for line in self.content.split('\n')]
        self.metrics = {}

        # Determine period from filename
        self.parse_period_from_filename()

    def parse_period_from_filename(self):
        """Extract period info from filename"""
        filename = self.file_path.name

        if "10K" in filename:
            match = re.search(r'FY(\d{4})', filename)
            if match:
                self.period = f"FY{match.group(1)}"
                self.is_annual = True
        elif "10Q" in filename:
            match = re.search(r'Q(\d)-FY(\d{4})', filename)
            if match:
                quarter = match.group(1)
                year = match.group(2)
                self.period = f"Q{quarter} FY{year}"
                self.is_annual = False

    def find_line(self, pattern: str, start_idx: int = 0, case_sensitive: bool = True) -> Optional[int]:
        """Find first line matching exact pattern"""
        flags = 0 if case_sensitive else re.IGNORECASE
        for i in range(start_idx, len(self.lines)):
            if re.match(pattern, self.lines[i], flags):
                return i
        return None

    def get_value_at(self, idx: int, offset: int = 1) -> Optional[float]:
        """Get numeric value at line idx + offset"""
        target_idx = idx + offset
        if target_idx >= len(self.lines):
            return None

        line = self.lines[target_idx]
        # Clean up the number
        line = line.replace('$', '').replace(',', '').strip()

        # Handle parentheses (negative numbers)
        if line.startswith('(') and line.endswith(')'):
            line = '-' + line[1:-1]

        try:
            return float(line)
        except ValueError:
            return None

    def find_value_after_label(self, label: str, start_idx: int = 0, max_offset: int = 5) -> Optional[float]:
        """Find a numeric value shortly after a label line"""
        idx = self.find_line(f'^{re.escape(label)}$', start_idx)
        if idx is None:
            return None

        # Try next few lines
        for offset in range(1, max_offset + 1):
            val = self.get_value_at(idx, offset)
            if val is not None:
                return val

        return None

    def parse_income_statement_10k(self):
        """Parse annual income statement (10-K)"""
        # Find the summary section that appears in MD&A around line 2622
        # Look for the pattern: Revenue / $ / NUMBER / $ / NUMBER / PERCENTAGE

        # Method 1: Find in the executive summary table
        revenue_idx = self.find_line('^Revenue$')
        if revenue_idx:
            # Next line is usually $, then the current year value
            self.metrics['Revenue'] = self.get_value_at(revenue_idx, 2)

        gross_margin_idx = self.find_line('^Gross margin$', revenue_idx if revenue_idx else 0)
        if gross_margin_idx:
            self.metrics['GrossProfit'] = self.get_value_at(gross_margin_idx, 1)

        op_income_idx = self.find_line('^Operating income$', gross_margin_idx if gross_margin_idx else 0)
        if op_income_idx:
            self.metrics['OperatingIncome'] = self.get_value_at(op_income_idx, 1)

        net_income_idx = self.find_line('^Net income$', op_income_idx if op_income_idx else 0)
        if net_income_idx:
            self.metrics['NetIncome'] = self.get_value_at(net_income_idx, 1)

        eps_idx = self.find_line('^Diluted earnings per share$', net_income_idx if net_income_idx else 0)
        if eps_idx:
            self.metrics['EPS'] = self.get_value_at(eps_idx, 1)

    def parse_income_statement_10q(self):
        """Parse quarterly income statement (10-Q)"""
        # Look for "INCOME STATEMENTS" section
        # Pattern: "Three Months Ended September 30," followed by years

        income_stmt_idx = self.find_line('INCOME STA', case_sensitive=False)
        if income_stmt_idx:
            # Find "Total revenue"
            revenue_idx = self.find_line('^Total revenue$', income_stmt_idx)
            if revenue_idx:
                self.metrics['Revenue'] = self.get_value_at(revenue_idx, 1)

            # Gross margin
            gross_margin_idx = self.find_line('^Gross margin$', revenue_idx if revenue_idx else income_stmt_idx)
            if gross_margin_idx:
                self.metrics['GrossProfit'] = self.get_value_at(gross_margin_idx, 1)

            # Operating income
            op_income_idx = self.find_line('^Operating income$', gross_margin_idx if gross_margin_idx else income_stmt_idx)
            if op_income_idx:
                self.metrics['OperatingIncome'] = self.get_value_at(op_income_idx, 1)

            # Net income
            net_income_idx = self.find_line('^Net income$', op_income_idx if op_income_idx else income_stmt_idx)
            if net_income_idx:
                self.metrics['NetIncome'] = self.get_value_at(net_income_idx, 1)

            # EPS diluted
            eps_idx = self.find_line('^Diluted$', net_income_idx if net_income_idx else income_stmt_idx)
            if eps_idx:
                self.metrics['EPS'] = self.get_value_at(eps_idx, 1)

    def parse_balance_sheet(self):
        """Parse balance sheet"""
        # Find balance sheet section
        bs_idx = self.find_line('BALANCE', case_sensitive=False)
        if bs_idx is None:
            return

        # Cash and equivalents (cash + short-term investments)
        cash_idx = self.find_line('^Cash and cash equivalents$', bs_idx)
        if cash_idx:
            cash = self.get_value_at(cash_idx, 2)
            st_inv_idx = self.find_line('^Short-term investments$', cash_idx)
            if st_inv_idx and st_inv_idx < cash_idx + 10:
                st_inv = self.get_value_at(st_inv_idx, 1)
                if cash is not None and st_inv is not None:
                    self.metrics['CashAndEquivalents'] = cash + st_inv

        # Shareholders' equity
        equity_idx = self.find_line('^Total stockholders[\'']? equity$', bs_idx)
        if equity_idx:
            self.metrics['ShareholdersEquity'] = self.get_value_at(equity_idx, 1)

        # Total debt (short-term + current portion of LT + long-term)
        total_debt = 0
        debt_found = False

        st_debt_idx = self.find_line('^Short-term debt$', bs_idx)
        if st_debt_idx:
            val = self.get_value_at(st_debt_idx, 1)
            if val is not None and val > 0:
                total_debt += val
                debt_found = True

        curr_ltd_idx = self.find_line('^Current portion of long-term debt$', bs_idx)
        if curr_ltd_idx:
            val = self.get_value_at(curr_ltd_idx, 1)
            if val is not None and val > 0:
                total_debt += val
                debt_found = True

        ltd_idx = self.find_line('^Long-term debt$', bs_idx)
        if ltd_idx:
            val = self.get_value_at(ltd_idx, 1)
            if val is not None and val > 0:
                total_debt += val
                debt_found = True

        if debt_found:
            self.metrics['TotalDebt'] = total_debt

    def parse_cash_flow(self):
        """Parse cash flow statement"""
        cf_idx = self.find_line('CASH FLOWS', case_sensitive=False)
        if cf_idx is None:
            return

        # Operating cash flow
        ocf_idx = self.find_line('^Net cash from operations$', cf_idx)
        if ocf_idx:
            self.metrics['OperatingCashFlow'] = self.get_value_at(ocf_idx, 1)

        # CapEx
        capex_idx = self.find_line('^Additions to property and equipment$', cf_idx)
        if capex_idx:
            val = self.get_value_at(capex_idx, 2)  # Usually has ( on next line, then number
            if val is not None:
                self.metrics['CapEx'] = abs(val)

        # Calculate FCF
        if self.metrics.get('OperatingCashFlow') is not None and self.metrics.get('CapEx') is not None:
            self.metrics['FreeCashFlow'] = self.metrics['OperatingCashFlow'] - self.metrics['CapEx']

    def parse_shares_outstanding(self):
        """Parse diluted shares outstanding"""
        # Look for "Weighted average shares outstanding:" section
        shares_idx = self.find_line('Weighted average shares outstanding:', case_sensitive=False)
        if shares_idx:
            diluted_idx = self.find_line('^Diluted$', shares_idx)
            if diluted_idx and diluted_idx < shares_idx + 10:
                shares = self.get_value_at(diluted_idx, 1)
                if shares:
                    self.metrics['SharesOutstanding'] = shares

    def parse_segment_revenue(self):
        """Parse segment revenue"""
        # Look for segment breakdown
        # Intelligent Cloud
        for i, line in enumerate(self.lines):
            if 'Intelligent Cloud' in line:
                # Look for Revenue label nearby
                for j in range(i+1, min(i+10, len(self.lines))):
                    if self.lines[j] == 'Revenue':
                        val = self.get_value_at(j, 2)
                        if val:
                            self.metrics['CloudRevenue'] = val
                        break
                break

        # Productivity and Business Processes
        for i, line in enumerate(self.lines):
            if 'Productivity and Business Processes' in line:
                for j in range(i+1, min(i+10, len(self.lines))):
                    if self.lines[j] == 'Revenue':
                        val = self.get_value_at(j, 2)
                        if val:
                            self.metrics['ProductivityRevenue'] = val
                        break
                break

        # More Personal Computing
        for i, line in enumerate(self.lines):
            if 'More Personal Computing' in line:
                for j in range(i+1, min(i+10, len(self.lines))):
                    if self.lines[j] == 'Revenue':
                        val = self.get_value_at(j, 2)
                        if val:
                            self.metrics['PersonalComputingRevenue'] = val
                        break
                break

    def calculate_margins(self):
        """Calculate margin percentages"""
        if self.metrics.get('Revenue'):
            rev = self.metrics['Revenue']
            if self.metrics.get('GrossProfit'):
                self.metrics['GrossMargin'] = round((self.metrics['GrossProfit'] / rev) * 100, 1)
            if self.metrics.get('OperatingIncome'):
                self.metrics['OperatingMargin'] = round((self.metrics['OperatingIncome'] / rev) * 100, 1)
            if self.metrics.get('NetIncome'):
                self.metrics['NetMargin'] = round((self.metrics['NetIncome'] / rev) * 100, 1)

    def parse(self) -> Dict:
        """Main parsing method"""
        print(f"Parsing {self.file_path.name} ({self.period})...")

        # Parse based on document type
        if self.is_annual:
            self.parse_income_statement_10k()
        else:
            self.parse_income_statement_10q()

        self.parse_balance_sheet()
        self.parse_cash_flow()
        self.parse_shares_outstanding()
        self.parse_segment_revenue()
        self.calculate_margins()

        # Add period
        self.metrics['Period'] = self.period

        # Debug: print what we found
        if self.metrics.get('Revenue'):
            print(f"  ✓ Revenue: ${self.metrics['Revenue']:.0f}M")
        else:
            print(f"  ✗ Revenue not found")

        return self.metrics


def parse_all_files():
    """Parse all extracted files"""
    all_metrics = []

    files = sorted(EXTRACTED_DIR.glob("MSFT_*.txt"))
    print(f"Found {len(files)} files to parse\n")

    for file_path in files:
        try:
            parser = MSFTMetricsParser(file_path)
            metrics = parser.parse()
            all_metrics.append(metrics)
        except Exception as e:
            print(f"ERROR parsing {file_path.name}: {e}")

    return all_metrics


def sort_periods(metrics_list: List[Dict]) -> List[Dict]:
    """Sort metrics chronologically"""
    def period_key(m):
        period = m.get('Period', '')
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 5)  # Sort annual after Q4
        elif period.startswith('Q'):
            match = re.match(r'Q(\d) FY(\d{4})', period)
            if match:
                quarter = int(match.group(1))
                year = int(match.group(2))
                return (year, quarter)
        return (0, 0)

    return sorted(metrics_list, key=period_key)


def write_csv(metrics_list: List[Dict], output_path: Path):
    """Write metrics to CSV"""
    columns = [
        'Period', 'Revenue', 'GrossProfit', 'GrossMargin',
        'OperatingIncome', 'OperatingMargin', 'NetIncome', 'NetMargin',
        'EPS', 'FreeCashFlow', 'OperatingCashFlow', 'CapEx',
        'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding',
        'CloudRevenue', 'ProductivityRevenue', 'PersonalComputingRevenue'
    ]

    metrics_list = sort_periods(metrics_list)

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for metrics in metrics_list:
            row = {col: metrics.get(col, '') for col in columns}
            writer.writerow(row)

    print(f"\n{'='*60}")
    print(f"✓ Wrote {len(metrics_list)} periods to:")
    print(f"  {output_path}")
    print(f"{'='*60}")


def main():
    """Main entry point"""
    print("="*60)
    print("Microsoft Financial Metrics Parser")
    print("="*60)
    print()

    metrics_list = parse_all_files()

    output_path = REPORTS_DIR / "MSFT_Metrics.csv"
    REPORTS_DIR.mkdir(exist_ok=True)
    write_csv(metrics_list, output_path)

    # Show summary
    print("\nSummary of recent periods:")
    print("-" * 60)
    for metrics in sorted(metrics_list, key=lambda x: x.get('Period', ''))[-5:]:
        period = metrics.get('Period', 'Unknown')
        revenue = metrics.get('Revenue', 0)
        net_income = metrics.get('NetIncome', 0)
        eps = metrics.get('EPS', 0)

        print(f"{period:12} | Rev: ${revenue:>8.0f}M | NI: ${net_income:>8.0f}M | EPS: ${eps:>5.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
