#!/usr/bin/env python3
"""
Parse Fiserv financial metrics from extracted SEC filings
"""

import os
import re
import csv
from pathlib import Path

# Manual data extraction based on reading the files
# This data is extracted from the consolidated financial statements

annual_data = {
    'FY2024': {
        'Revenue': 20456,
        'ProcessingRevenue': 16637,
        'ProductRevenue': 3819,
        'OperatingIncome': 5879,
        'NetIncome': 3131,
        'EPS': 5.38,
        'OperatingCashFlow': 6631,
        'CapEx': 1569,
        'CashAndEquivalents': 1236,
        'TotalDebt': 24840,  # 23730 + 1110
        'ShareholdersEquity': 27068,
        'SharesOutstanding': 582.1,
        'MerchantRevenue': 9631,
        'FinancialRevenue': 9477,
    },
    'FY2023': {
        'Revenue': 19093,
        'ProcessingRevenue': 15630,
        'ProductRevenue': 3463,
        'OperatingIncome': 5014,
        'NetIncome': 3068,
        'EPS': 4.98,
        'OperatingCashFlow': 5162,
        'CapEx': 1388,
        'CashAndEquivalents': 1204,
        'TotalDebt': 23118,  # 22363 + 755
        'ShareholdersEquity': 29857,
        'SharesOutstanding': 615.9,
        'MerchantRevenue': 8722,
        'FinancialRevenue': 9101,
    },
    'FY2022': {
        'Revenue': 17737,
        'ProcessingRevenue': 14460,
        'ProductRevenue': 3277,
        'OperatingIncome': 3740,
        'NetIncome': 2530,
        'EPS': 3.91,
        'OperatingCashFlow': 4618,
        'CapEx': 1479,
        'CashAndEquivalents': 902,
        'TotalDebt': None,  # Need to find
        'ShareholdersEquity': None,  # Need to find
        'SharesOutstanding': 647.9,
        'MerchantRevenue': 7883,
        'FinancialRevenue': 8681,
    },
    'FY2021': {
        'Revenue': 16226,
        'ProcessingRevenue': 13307,
        'ProductRevenue': 2919,
        'OperatingIncome': 2288,
        'NetIncome': 1334,
        'EPS': 1.99,
        'OperatingCashFlow': None,  # Need to find
        'CapEx': None,
        'CashAndEquivalents': 835,
        'TotalDebt': 21237,  # 20729 + 508
        'ShareholdersEquity': None,  # Need to calculate from balance sheet
        'SharesOutstanding': 671.6,
        'MerchantRevenue': None,  # Different segment structure
        'FinancialRevenue': None,
    },
    'FY2020': {
        'Revenue': 14852,
        'ProcessingRevenue': 12215,
        'ProductRevenue': 2637,
        'OperatingIncome': 1852,
        'NetIncome': 958,
        'EPS': 1.40,
        'OperatingCashFlow': None,
        'CapEx': None,
        'CashAndEquivalents': 906,
        'TotalDebt': 20684,  # 20300 + 384
        'ShareholdersEquity': None,
        'SharesOutstanding': 683.4,
        'MerchantRevenue': None,
        'FinancialRevenue': None,
    },
    'FY2019': {
        'Revenue': 10187,
        'ProcessingRevenue': 8573,
        'ProductRevenue': 1614,
        'OperatingIncome': 1609,
        'NetIncome': 893,
        'EPS': 1.71,
        'OperatingCashFlow': None,
        'CapEx': None,
        'CashAndEquivalents': 893,
        'TotalDebt': None,
        'ShareholdersEquity': None,
        'SharesOutstanding': 522.6,
        'MerchantRevenue': None,
        'FinancialRevenue': None,
    },
    'FY2018': {
        'Revenue': 5823,
        'ProcessingRevenue': 4975,
        'ProductRevenue': 848,
        'OperatingIncome': 1753,
        'NetIncome': 1187,
        'EPS': 2.87,
        'OperatingCashFlow': None,
        'CapEx': None,
        'CashAndEquivalents': None,
        'TotalDebt': None,
        'ShareholdersEquity': None,
        'SharesOutstanding': 413.7,
        'MerchantRevenue': None,
        'FinancialRevenue': None,
    },
}

def calculate_derived_metrics(data):
    """Calculate derived metrics like margins and FCF"""
    result = data.copy()

    # Calculate margins
    if data.get('Revenue') and data.get('OperatingIncome'):
        result['OperatingMargin'] = round((data['OperatingIncome'] / data['Revenue']) * 100, 1)
    else:
        result['OperatingMargin'] = None

    if data.get('Revenue') and data.get('NetIncome'):
        result['NetMargin'] = round((data['NetIncome'] / data['Revenue']) * 100, 1)
    else:
        result['NetMargin'] = None

    # Calculate gross profit (approximation - FISV doesn't report this clearly)
    # Gross Profit = Revenue - (Cost of processing + Cost of product)
    result['GrossProfit'] = None
    result['GrossMargin'] = None

    # Calculate FCF
    if data.get('OperatingCashFlow') and data.get('CapEx'):
        result['FreeCashFlow'] = data['OperatingCashFlow'] - data['CapEx']
    else:
        result['FreeCashFlow'] = None

    return result

def create_csv():
    """Create CSV file with all metrics"""

    output_dir = Path('/Users/swilliams/Stocks/Research/FISV/Reports')
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / 'FISV_Metrics.csv'

    # Define CSV columns
    columns = [
        'Period',
        'Revenue',
        'GrossProfit',
        'GrossMargin',
        'OperatingIncome',
        'OperatingMargin',
        'NetIncome',
        'NetMargin',
        'EPS',
        'FreeCashFlow',
        'OperatingCashFlow',
        'CapEx',
        'ShareholdersEquity',
        'TotalDebt',
        'CashAndEquivalents',
        'SharesOutstanding',
        'MerchantRevenue',
        'FinancialRevenue',
        'ProcessingRevenue',
        'ProductRevenue',
    ]

    # Sort periods chronologically
    periods = sorted(annual_data.keys(), key=lambda x: int(x[2:]))

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for period in periods:
            data = calculate_derived_metrics(annual_data[period])
            row = {
                'Period': period,
                'Revenue': data.get('Revenue'),
                'GrossProfit': data.get('GrossProfit'),
                'GrossMargin': data.get('GrossMargin'),
                'OperatingIncome': data.get('OperatingIncome'),
                'OperatingMargin': data.get('OperatingMargin'),
                'NetIncome': data.get('NetIncome'),
                'NetMargin': data.get('NetMargin'),
                'EPS': data.get('EPS'),
                'FreeCashFlow': data.get('FreeCashFlow'),
                'OperatingCashFlow': data.get('OperatingCashFlow'),
                'CapEx': data.get('CapEx'),
                'ShareholdersEquity': data.get('ShareholdersEquity'),
                'TotalDebt': data.get('TotalDebt'),
                'CashAndEquivalents': data.get('CashAndEquivalents'),
                'SharesOutstanding': data.get('SharesOutstanding'),
                'MerchantRevenue': data.get('MerchantRevenue'),
                'FinancialRevenue': data.get('FinancialRevenue'),
                'ProcessingRevenue': data.get('ProcessingRevenue'),
                'ProductRevenue': data.get('ProductRevenue'),
            }

            # Convert None to empty string for CSV
            row = {k: ('' if v is None else v) for k, v in row.items()}
            writer.writerow(row)

    print(f"Created {output_file}")
    print(f"Wrote {len(periods)} annual periods")

if __name__ == '__main__':
    create_csv()
