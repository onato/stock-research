#!/usr/bin/env python3
"""
Parse Uber SEC filings (HTML with inline XBRL) to extract financial metrics.
Uses BeautifulSoup for proper HTML parsing.
"""

import re
import csv
from pathlib import Path
from decimal import Decimal

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    print("Warning: BeautifulSoup not available, using regex fallback")

def clean_number(text):
    """Clean and convert number text to float."""
    if not text:
        return None
    # Remove common formatting
    text = str(text).strip().replace(',', '').replace('$', '').replace('%', '')
    # Handle parentheses as negative
    if '(' in text and ')' in text:
        text = '-' + text.replace('(', '').replace(')', '')
    # Remove any remaining non-numeric characters except . and -
    text = re.sub(r'[^\d.-]', '', text)
    try:
        return float(text)
    except (ValueError, TypeError):
        return None

def parse_with_bs4(filepath):
    """Parse using BeautifulSoup for proper HTML handling."""
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        soup = BeautifulSoup(f, 'html.parser')

    data = {}

    # Find all inline XBRL nonfraction tags
    for tag in soup.find_all(['ix:nonfraction', 'ix:nonfrac', 'span']):
        name_attr = tag.get('name', '')
        if not name_attr:
            continue

        # Extract the tag name after the colon
        if ':' in name_attr:
            tag_name = name_attr.split(':')[-1]
        else:
            tag_name = name_attr

        # Get the value
        value = clean_number(tag.get_text())
        if value is not None:
            # Store with context ref for period identification
            context = tag.get('contextref', '')
            if tag_name not in data:
                data[tag_name] = []
            data[tag_name].append((context, value))

    return data

def parse_with_regex(filepath):
    """Fallback parsing using regex."""
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    data = {}

    # Pattern for inline XBRL: <tag name="prefix:TagName" contextRef="context">value</tag>
    pattern = r'<[^>]*name=["\']([^:"\']+):([^"\']+)["\'][^>]*contextRef=["\']([^"\']+)["\'][^>]*>([^<]+)</[^>]*>'
    matches = re.findall(pattern, content)

    for prefix, tag_name, context, value_text in matches:
        value = clean_number(value_text)
        if value is not None:
            if tag_name not in data:
                data[tag_name] = []
            data[tag_name].append((context, value))

    return data

def select_best_value(values_list, prefer_context=None):
    """Select the most appropriate value from a list of (context, value) tuples."""
    if not values_list:
        return None

    # If prefer_context is specified, try to find that context first
    if prefer_context:
        for context, value in values_list:
            if prefer_context in context.lower():
                return value

    # Otherwise, return the first value (most common approach)
    return values_list[0][1]

def parse_file(filepath):
    """Parse a single SEC filing."""
    print(f"Parsing {filepath.name}...")

    # Extract period from filename
    filename = filepath.stem
    period_match = re.search(r'(Q[1-3]|FY)[-_](\d{4})', filename)
    if not period_match:
        print(f"  Could not extract period from filename")
        return None

    period_type = period_match.group(1)
    year = period_match.group(2)
    if period_type == 'FY':
        period = f"FY{year}"
        context_hint = 'fy'
    else:
        period = f"{period_type} {year}"
        context_hint = f'{period_type.lower()}{year}'

    print(f"  Period: {period}")

    # Parse the file
    if HAS_BS4:
        xbrl_data = parse_with_bs4(filepath)
    else:
        xbrl_data = parse_with_regex(filepath)

    if not xbrl_data:
        print(f"  No XBRL data found")
        return None

    print(f"  Found {len(xbrl_data)} unique tags")

    # Map XBRL tags to our metrics
    tag_mappings = {
        'Revenue': ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'RevenueFromContractWithCustomerIncludingAssessedTax'],
        'CostOfRevenue': ['CostOfRevenue', 'CostOfGoodsAndServicesSold'],
        'GrossProfit': ['GrossProfit'],
        'OperatingExpenses': ['OperatingExpenses', 'OperatingExpensesTotal'],
        'OperatingIncome': ['OperatingIncomeLoss'],
        'NetIncome': ['NetIncomeLoss', 'ProfitLoss'],
        'EPS': ['EarningsPerShareDiluted'],
        'OperatingCashFlow': ['NetCashProvidedByUsedInOperatingActivities'],
        'CapEx': ['PaymentsToAcquirePropertyPlantAndEquipment'],
        'ShareholdersEquity': ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
        'LongTermDebt': ['LongTermDebt', 'LongTermDebtNoncurrent'],
        'ShortTermDebt': ['ShortTermBorrowings', 'LongTermDebtCurrent', 'ShortTermDebt'],
        'CashAndEquivalents': ['CashAndCashEquivalentsAtCarryingValue', 'Cash'],
        'SharesOutstanding': ['WeightedAverageNumberOfDilutedSharesOutstanding', 'WeightedAverageNumberOfSharesOutstandingDiluted', 'CommonStockSharesOutstanding'],
    }

    # Extract values
    raw_metrics = {}
    for metric_name, possible_tags in tag_mappings.items():
        for tag in possible_tags:
            if tag in xbrl_data:
                value = select_best_value(xbrl_data[tag], context_hint)
                if value is not None:
                    raw_metrics[metric_name] = value
                    break

    # Build final metrics dictionary
    metrics = {
        'Period': period,
        'Revenue': None,
        'CostOfRevenue': None,
        'GrossProfit': None,
        'GrossMargin': None,
        'OperatingExpenses': None,
        'OperatingIncome': None,
        'NetIncome': None,
        'EPS': None,
        'FreeCashFlow': None,
        'GrossBookings': None,
        'MAPCs': None,
        'Trips': None,
        'MobilityRevenue': None,
        'DeliveryRevenue': None,
        'FreightRevenue': None,
        'AdjustedEBITDA': None,
        'ShareholdersEquity': None,
        'TotalDebt': None,
        'CashAndEquivalents': None,
        'SharesOutstanding': None,
    }

    # Convert to millions (XBRL values are in actual dollars)
    # EPS is already per-share, shares are in actual count
    for key, value in raw_metrics.items():
        if value is not None:
            if key == 'EPS':
                metrics[key] = round(value, 2)
            elif key == 'SharesOutstanding':
                metrics[key] = round(value / 1_000_000, 1)  # Convert to millions of shares
            else:
                metrics[key] = round(value / 1_000_000, 1)  # Convert to millions of dollars

    # Calculate derived metrics
    if metrics['Revenue'] and metrics['CostOfRevenue']:
        if not metrics['GrossProfit']:
            metrics['GrossProfit'] = round(metrics['Revenue'] - metrics['CostOfRevenue'], 1)

    if metrics['Revenue'] and metrics['GrossProfit']:
        metrics['GrossMargin'] = round((metrics['GrossProfit'] / metrics['Revenue']) * 100, 1)

    # Free Cash Flow = Operating Cash Flow - CapEx
    if raw_metrics.get('OperatingCashFlow') and raw_metrics.get('CapEx'):
        fcf = (raw_metrics['OperatingCashFlow'] - raw_metrics['CapEx']) / 1_000_000
        metrics['FreeCashFlow'] = round(fcf, 1)

    # Total Debt = Long-term + Short-term
    ltd = raw_metrics.get('LongTermDebt', 0)
    std = raw_metrics.get('ShortTermDebt', 0)
    if ltd or std:
        metrics['TotalDebt'] = round((ltd + std) / 1_000_000, 1)

    found_count = sum(1 for v in metrics.values() if v is not None and v != period)
    print(f"  Extracted {found_count} metrics")

    if metrics['Revenue'] is None:
        print(f"  WARNING: No revenue found!")
        # Print available tags for debugging
        print(f"  Available tags sample: {list(xbrl_data.keys())[:20]}")

    return metrics

def main():
    # Directory paths
    extracted_dir = Path('/Users/swilliams/Stocks/Research/UBER/Extracted')
    reports_dir = Path('/Users/swilliams/Stocks/Research/UBER/Reports')
    reports_dir.mkdir(exist_ok=True)

    # Parse all files
    all_metrics = []

    for filepath in sorted(extracted_dir.glob('UBER_*.txt')):
        metrics = parse_file(filepath)
        if metrics:
            all_metrics.append(metrics)

    if not all_metrics:
        print("\nNo metrics extracted from any file!")
        return 1

    # Sort by period
    def sort_key(m):
        period = m['Period']
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 4)
        elif period.startswith('Q'):
            quarter_match = re.match(r'Q([1-3]) (\d{4})', period)
            if quarter_match:
                quarter = int(quarter_match.group(1))
                year = int(quarter_match.group(2))
                return (year, quarter)
        return (0, 0)

    all_metrics.sort(key=sort_key)

    # Write CSV
    csv_path = reports_dir / 'UBER_Metrics.csv'

    fieldnames = [
        'Period', 'Revenue', 'CostOfRevenue', 'GrossProfit', 'GrossMargin',
        'OperatingExpenses', 'OperatingIncome', 'NetIncome', 'EPS', 'FreeCashFlow',
        'GrossBookings', 'MAPCs', 'Trips', 'MobilityRevenue', 'DeliveryRevenue',
        'FreightRevenue', 'AdjustedEBITDA', 'ShareholdersEquity', 'TotalDebt',
        'CashAndEquivalents', 'SharesOutstanding'
    ]

    with open(csv_path, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_metrics)

    print(f"\n{'='*70}")
    print(f"Successfully wrote {len(all_metrics)} periods to:")
    print(f"{csv_path}")
    print(f"{'='*70}")

    # Print summary
    print("\nPeriods with revenue data:")
    for m in all_metrics:
        if m['Revenue']:
            ni = m['NetIncome'] if m['NetIncome'] is not None else 'N/A'
            print(f"  {m['Period']:12} Revenue: ${m['Revenue']:>8}M  NetIncome: ${ni}M" if isinstance(ni, (int, float)) else f"  {m['Period']:12} Revenue: ${m['Revenue']:>8}M  NetIncome: {ni}")

    print("\nNote: Many fields may be empty. Uber-specific KPIs (Gross Bookings, MAPCs, etc.)")
    print("      may need manual extraction from earnings releases or investor presentations.")

    return 0

if __name__ == '__main__':
    exit(main())
