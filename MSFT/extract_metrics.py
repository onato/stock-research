#!/usr/bin/env python3
"""
Manual data extraction for Microsoft metrics.
This script contains hardcoded data extracted from the SEC filings.
"""

import csv
from pathlib import Path

BASE_DIR = Path("/Users/swilliams/Stocks/Research/MSFT")
REPORTS_DIR = BASE_DIR / "Reports"

# Manually extracted data from SEC filings
# All amounts in millions USD except EPS and percentages
METRICS_DATA = [
    # FY2015-2020 (to be filled from 10-K files)
    {
        'Period': 'FY2015',
        'Revenue': 93580,
        'GrossProfit': 64470,
        'OperatingIncome': 18161,
        'NetIncome': 12193,
        'EPS': 1.48,
    },
    {
        'Period': 'FY2016',
        'Revenue': 85320,
        'GrossProfit': 58374,
        'OperatingIncome': 20182,
        'NetIncome': 16798,
        'EPS': 2.10,
    },
    {
        'Period': 'FY2017',
        'Revenue': 89950,
        'GrossProfit': 55689,
        'OperatingIncome': 22326,
        'NetIncome': 21204,
        'EPS': 2.71,
    },
    {
        'Period': 'FY2018',
        'Revenue': 110360,
        'GrossProfit': 72007,
        'OperatingIncome': 35058,
        'NetIncome': 16571,
        'EPS': 2.13,
    },
    {
        'Period': 'FY2019',
        'Revenue': 125843,
        'GrossProfit': 82933,
        'OperatingIncome': 42959,
        'NetIncome': 39240,
        'EPS': 5.06,
    },
    {
        'Period': 'FY2020',
        'Revenue': 143015,
        'GrossProfit': 96937,
        'OperatingIncome': 52959,
        'NetIncome': 44281,
        'EPS': 5.76,
    },
    {
        'Period': 'FY2021',
        'Revenue': 168088,
        'GrossProfit': 115856,
        'OperatingIncome': 69916,
        'NetIncome': 61271,
        'EPS': 8.05,
    },
    {
        'Period': 'FY2022',
        'Revenue': 198270,
        'GrossProfit': 135620,
        'OperatingIncome': 83383,
        'NetIncome': 72738,
        'EPS': 9.65,
    },
    # FY2023-2025 (extracted from MSFT_10K_FY2025.txt lines 3115-3213)
    {
        'Period': 'FY2023',
        'Revenue': 211915,
        'GrossProfit': 146052,
        'OperatingIncome': 88523,
        'NetIncome': 72361,
        'EPS': 9.68,
        'SharesOutstanding': 7472,
    },
    {
        'Period': 'FY2024',
        'Revenue': 245122,
        'GrossProfit': 171008,
        'OperatingIncome': 109433,
        'NetIncome': 88136,
        'EPS': 11.80,
        'SharesOutstanding': 7469,
    },
    {
        'Period': 'FY2025',
        'Revenue': 281724,
        'GrossProfit': 193893,
        'OperatingIncome': 128528,
        'NetIncome': 101832,
        'EPS': 13.64,
        'SharesOutstanding': 7465,
    },
]

def calculate_margins(metrics):
    """Add margin calculations"""
    if metrics.get('Revenue'):
        rev = metrics['Revenue']
        if metrics.get('GrossProfit'):
            metrics['GrossMargin'] = round((metrics['GrossProfit'] / rev) * 100, 1)
        if metrics.get('OperatingIncome'):
            metrics['OperatingMargin'] = round((metrics['OperatingIncome'] / rev) * 100, 1)
        if metrics.get('NetIncome'):
            metrics['NetMargin'] = round((metrics['NetIncome'] / rev) * 100, 1)
    return metrics

def write_csv():
    """Write metrics to CSV"""
    columns = [
        'Period', 'Revenue', 'GrossProfit', 'GrossMargin',
        'OperatingIncome', 'OperatingMargin', 'NetIncome', 'NetMargin',
        'EPS', 'FreeCashFlow', 'OperatingCashFlow', 'CapEx',
        'ShareholdersEquity', 'TotalDebt', 'CashAndEquivalents', 'SharesOutstanding',
        'CloudRevenue', 'ProductivityRevenue', 'PersonalComputingRevenue'
    ]

    # Calculate margins
    for metrics in METRICS_DATA:
        calculate_margins(metrics)

    # Write CSV
    output_path = REPORTS_DIR / "MSFT_Metrics.csv"
    REPORTS_DIR.mkdir(exist_ok=True)

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for metrics in METRICS_DATA:
            row = {col: metrics.get(col, '') for col in columns}
            writer.writerow(row)

    print(f"Wrote {len(METRICS_DATA)} periods to {output_path}")
    return output_path

if __name__ == "__main__":
    output_path = write_csv()
    print(f"\nCSV file created: {output_path}")
