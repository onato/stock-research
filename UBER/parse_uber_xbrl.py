#!/usr/bin/env python3
"""
Parse Uber XBRL files to extract financial metrics for DCF analysis.
"""

import xml.etree.ElementTree as ET
import csv
import re
from pathlib import Path
from collections import defaultdict
import sys

# XBRL namespaces commonly used
NAMESPACES = {
    'us-gaap': 'http://fasb.org/us-gaap/2023',
    'us-gaap-2024': 'http://fasb.org/us-gaap/2024',
    'us-gaap-2022': 'http://fasb.org/us-gaap/2022',
    'us-gaap-2021': 'http://fasb.org/us-gaap/2021',
    'us-gaap-2020': 'http://fasb.org/us-gaap/2020',
    'uber': 'http://www.uber.com/20231231',
    'dei': 'http://xbrl.sec.gov/dei/2023',
}

def parse_xbrl_file(filepath):
    """Parse an XBRL file and extract financial metrics."""
    print(f"Parsing {filepath.name}...")

    try:
        # Read the file content
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Try to parse as XML
        root = ET.fromstring(content)

        # Extract period from filename
        filename = filepath.stem
        period_match = re.search(r'(Q[1-3]|FY)[-_](\d{4})', filename)
        if period_match:
            period_type = period_match.group(1)
            year = period_match.group(2)
            if period_type == 'FY':
                period = f"FY{year}"
            else:
                period = f"{period_type} {year}"
        else:
            period = filename

        # Dictionary to store metrics
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

        # Find all elements with contextRef (for period-specific data)
        # We need to identify the correct context for quarterly or annual data
        contexts = {}
        for elem in root.iter():
            if 'contextRef' in elem.attrib:
                context_ref = elem.attrib['contextRef']
                tag_name = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag

                # Try to get the value
                try:
                    value = float(elem.text) if elem.text else None
                    if value is not None:
                        if context_ref not in contexts:
                            contexts[context_ref] = {}
                        contexts[context_ref][tag_name] = value
                except (ValueError, TypeError):
                    pass

        # Now find the right context (usually contains period indicators)
        # For quarterly: look for 3-month periods
        # For annual: look for 12-month periods

        # Common XBRL tag mappings
        tag_mappings = {
            'Revenues': 'Revenue',
            'RevenueFromContractWithCustomerExcludingAssessedTax': 'Revenue',
            'CostOfRevenue': 'CostOfRevenue',
            'CostOfGoodsAndServicesSold': 'CostOfRevenue',
            'GrossProfit': 'GrossProfit',
            'OperatingExpenses': 'OperatingExpenses',
            'OperatingIncomeLoss': 'OperatingIncome',
            'NetIncomeLoss': 'NetIncome',
            'EarningsPerShareDiluted': 'EPS',
            'NetCashProvidedByUsedInOperatingActivities': 'OperatingCashFlow',
            'PaymentsToAcquirePropertyPlantAndEquipment': 'CapEx',
            'StockholdersEquity': 'ShareholdersEquity',
            'LongTermDebt': 'LongTermDebt',
            'ShortTermDebt': 'ShortTermDebt',
            'CashAndCashEquivalentsAtCarryingValue': 'CashAndEquivalents',
            'WeightedAverageNumberOfDilutedSharesOutstanding': 'SharesOutstanding',
        }

        # Look through contexts to find relevant data
        # Priority: most recent period data
        best_context = None
        for context_ref, data in contexts.items():
            if 'Revenues' in data or 'RevenueFromContractWithCustomerExcludingAssessedTax' in data:
                best_context = context_ref
                break

        if best_context:
            data = contexts[best_context]
            for xbrl_tag, metric_name in tag_mappings.items():
                if xbrl_tag in data:
                    metrics[metric_name] = data[xbrl_tag]

        # Calculate derived metrics
        if metrics['Revenue'] and metrics['CostOfRevenue']:
            if not metrics['GrossProfit']:
                metrics['GrossProfit'] = metrics['Revenue'] - metrics['CostOfRevenue']
            metrics['GrossMargin'] = round((metrics['GrossProfit'] / metrics['Revenue']) * 100, 1)

        # Convert to millions (XBRL values are typically in actual dollars)
        for key in metrics:
            if key not in ['Period', 'GrossMargin', 'EPS', 'MAPCs', 'Trips'] and metrics[key] is not None:
                metrics[key] = round(metrics[key] / 1_000_000, 1)

        return metrics

    except Exception as e:
        print(f"Error parsing {filepath.name}: {e}")
        return None

def main():
    # Directory paths
    extracted_dir = Path('/Users/swilliams/Stocks/Research/UBER/Extracted')
    reports_dir = Path('/Users/swilliams/Stocks/Research/UBER/Reports')
    reports_dir.mkdir(exist_ok=True)

    # Parse all files
    all_metrics = []

    for filepath in sorted(extracted_dir.glob('UBER_*.txt')):
        metrics = parse_xbrl_file(filepath)
        if metrics:
            all_metrics.append(metrics)

    # Sort by period
    def sort_key(m):
        period = m['Period']
        if period.startswith('FY'):
            year = int(period[2:])
            return (year, 4)  # Annual reports at end of year
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

    if all_metrics:
        fieldnames = list(all_metrics[0].keys())

        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_metrics)

        print(f"\nSuccessfully wrote {len(all_metrics)} records to {csv_path}")
    else:
        print("No metrics extracted!")

if __name__ == '__main__':
    main()
