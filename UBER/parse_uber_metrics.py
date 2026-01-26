#!/usr/bin/env python3
"""
Parse Uber SEC filings (HTML with inline XBRL) to extract financial metrics.
"""

import re
import csv
from pathlib import Path
from collections import defaultdict

def extract_xbrl_values(content, tag_patterns, context_pattern=None):
    """Extract XBRL values from HTML content using regex patterns."""
    results = {}

    for tag_name, pattern_list in tag_patterns.items():
        if isinstance(pattern_list, str):
            pattern_list = [pattern_list]

        for pattern in pattern_list:
            # Look for inline XBRL format: <ix:nonfraction ... name="us-gaap:TagName" ...>value</ix:nonfraction>
            # or <span ... name="us-gaap:TagName" ...>value</span>
            regex_patterns = [
                rf'<(?:ix:nonfraction|ix:nonFraction|span)[^>]*name=["\'](?:us-gaap|uber):{pattern}["\'][^>]*>([^<]+)</(?:ix:nonfraction|ix:nonFraction|span)>',
                rf'<(?:us-gaap|uber):{pattern}[^>]*contextRef=["\']([^"\']+)["\'][^>]*>([^<]+)</(?:us-gaap|uber):{pattern}>',
            ]

            for regex in regex_patterns:
                matches = re.findall(regex, content, re.IGNORECASE)
                if matches:
                    # Get the first match value
                    if len(matches[0]) == 1:
                        value_str = matches[0]
                    else:
                        value_str = matches[0][-1]  # Last group is the value

                    # Clean and convert value
                    value_str = value_str.strip().replace(',', '').replace('$', '').replace('(', '-').replace(')', '')

                    try:
                        value = float(value_str)
                        results[tag_name] = value
                        break  # Found a value for this tag
                    except ValueError:
                        continue

            if tag_name in results:
                break  # Move to next tag

    return results

def parse_file(filepath):
    """Parse a single filing to extract metrics."""
    print(f"Parsing {filepath.name}...")

    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

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
            return None

        print(f"  Period: {period}")

        # Define tag patterns to search for
        # Using multiple possible tag names for each metric
        tag_patterns = {
            'Revenue': ['Revenues?', 'RevenueFromContractWithCustomerExcludingAssessedTax'],
            'CostOfRevenue': ['CostOfRevenue', 'CostOfGoodsAndServicesSold', 'CostsAndExpenses'],
            'GrossProfit': ['GrossProfit'],
            'OperatingExpenses': ['OperatingExpenses', 'OperatingExpensesTotal'],
            'OperatingIncome': ['OperatingIncomeLoss'],
            'NetIncome': ['NetIncomeLoss', 'ProfitLoss'],
            'EPS': ['EarningsPerShareDiluted'],
            'OperatingCashFlow': ['NetCashProvidedByUsedInOperatingActivities'],
            'CapEx': ['PaymentsToAcquirePropertyPlantAndEquipment'],
            'ShareholdersEquity': ['StockholdersEquity'],
            'LongTermDebt': ['LongTermDebt', 'LongTermDebtNoncurrent'],
            'ShortTermDebt': ['ShortTermBorrowings', 'LongTermDebtCurrent'],
            'CashAndEquivalents': ['CashAndCashEquivalentsAtCarryingValue', 'Cash'],
            'SharesOutstanding': ['WeightedAverageNumberOfDilutedSharesOutstanding', 'CommonStockSharesOutstanding'],
        }

        # Extract values
        raw_data = extract_xbrl_values(content, tag_patterns)

        # Look for Uber-specific metrics using different patterns
        # Gross Bookings
        gb_patterns = [
            r'Gross\s+Bookings[^>]*>[\s$]*([0-9,]+)',
            r'name=["\']uber:GrossBookings["\'][^>]*>([^<]+)<',
        ]
        for pattern in gb_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                try:
                    val = matches[0].replace(',', '').replace('$', '').strip()
                    raw_data['GrossBookings'] = float(val)
                    break
                except:
                    pass

        # MAPCs (Monthly Active Platform Consumers) - in millions already
        mapc_patterns = [
            r'Monthly\s+Active\s+Platform\s+Consumers[^>]*>[\s]*([0-9,]+)',
            r'name=["\']uber:MonthlyActivePlatformConsumers["\'][^>]*>([^<]+)<',
        ]
        for pattern in mapc_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                try:
                    val = matches[0].replace(',', '').strip()
                    raw_data['MAPCs'] = float(val)
                    break
                except:
                    pass

        # Trips
        trips_patterns = [
            r'Trips["\'][^>]*>[\s]*([0-9,]+)',
            r'name=["\']uber:Trips["\'][^>]*>([^<]+)<',
        ]
        for pattern in trips_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                try:
                    val = matches[0].replace(',', '').strip()
                    raw_data['Trips'] = float(val)
                    break
                except:
                    pass

        # Segment revenues
        mobility_patterns = [
            r'Mobility[^<]*revenue[^>]*>[\s$]*([0-9,]+)',
            r'name=["\'](?:uber|us-gaap):.*Mobility.*Revenue["\'][^>]*>([^<]+)<',
        ]
        for pattern in mobility_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                try:
                    val = matches[0].replace(',', '').replace('$', '').strip()
                    raw_data['MobilityRevenue'] = float(val)
                    break
                except:
                    pass

        delivery_patterns = [
            r'Delivery[^<]*revenue[^>]*>[\s$]*([0-9,]+)',
            r'name=["\'](?:uber|us-gaap):.*Delivery.*Revenue["\'][^>]*>([^<]+)<',
        ]
        for pattern in delivery_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                try:
                    val = matches[0].replace(',', '').replace('$', '').strip()
                    raw_data['DeliveryRevenue'] = float(val)
                    break
                except:
                    pass

        freight_patterns = [
            r'Freight[^<]*revenue[^>]*>[\s$]*([0-9,]+)',
            r'name=["\'](?:uber|us-gaap):.*Freight.*Revenue["\'][^>]*>([^<]+)<',
        ]
        for pattern in freight_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                try:
                    val = matches[0].replace(',', '').replace('$', '').strip()
                    raw_data['FreightRevenue'] = float(val)
                    break
                except:
                    pass

        # Adjusted EBITDA
        ebitda_patterns = [
            r'Adjusted\s+EBITDA[^>]*>[\s$\(]*([0-9,]+)',
            r'name=["\']uber:AdjustedEBITDA["\'][^>]*>([^<]+)<',
        ]
        for pattern in ebitda_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                try:
                    val = matches[0].replace(',', '').replace('$', '').replace('(', '-').replace(')', '').strip()
                    raw_data['AdjustedEBITDA'] = float(val)
                    break
                except:
                    pass

        # Build metrics dictionary
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

        # Convert values to millions (except EPS, MAPCs, Trips which are already in correct units)
        for key, value in raw_data.items():
            if value is not None:
                if key in ['EPS', 'MAPCs', 'Trips']:
                    metrics[key] = round(value, 2)
                else:
                    # Convert from actual dollars to millions
                    metrics[key] = round(value / 1_000_000, 1)

        # Calculate derived metrics
        if metrics['Revenue'] and metrics['CostOfRevenue']:
            if not metrics['GrossProfit']:
                metrics['GrossProfit'] = round(metrics['Revenue'] - metrics['CostOfRevenue'], 1)

        if metrics['Revenue'] and metrics['GrossProfit']:
            metrics['GrossMargin'] = round((metrics['GrossProfit'] / metrics['Revenue']) * 100, 1)

        # Calculate Free Cash Flow
        if 'OperatingCashFlow' in raw_data and 'CapEx' in raw_data:
            ocf = raw_data['OperatingCashFlow'] / 1_000_000
            capex = raw_data['CapEx'] / 1_000_000
            metrics['FreeCashFlow'] = round(ocf - capex, 1)

        # Calculate Total Debt
        if 'LongTermDebt' in raw_data and 'ShortTermDebt' in raw_data:
            ltd = raw_data['LongTermDebt'] / 1_000_000
            std = raw_data['ShortTermDebt'] / 1_000_000
            metrics['TotalDebt'] = round(ltd + std, 1)
        elif 'LongTermDebt' in raw_data:
            metrics['TotalDebt'] = round(raw_data['LongTermDebt'] / 1_000_000, 1)

        print(f"  Found {sum(1 for v in metrics.values() if v is not None)} metrics")

        return metrics

    except Exception as e:
        print(f"Error parsing {filepath.name}: {e}")
        import traceback
        traceback.print_exc()
        return None

def main():
    # Directory paths
    extracted_dir = Path('/Users/swilliams/Stocks/Research/UBER/Extracted')
    reports_dir = Path('/Users/swilliams/Stocks/Research/UBER/Reports')
    reports_dir.mkdir(exist_ok=True)

    # Parse all files
    all_metrics = []

    for filepath in sorted(extracted_dir.glob('UBER_*.txt')):
        metrics = parse_file(filepath)
        if metrics and metrics['Revenue']:  # Only include if we got revenue data
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

        print(f"\n{'='*60}")
        print(f"Successfully wrote {len(all_metrics)} periods to:")
        print(f"{csv_path}")
        print(f"{'='*60}")

        # Print summary
        print("\nData summary:")
        for m in all_metrics:
            print(f"  {m['Period']}: Revenue=${m['Revenue']}M, NetIncome=${m['NetIncome']}M")
    else:
        print("No metrics extracted!")
        return 1

    return 0

if __name__ == '__main__':
    exit(main())
