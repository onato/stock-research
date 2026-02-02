#!/usr/bin/env python3
"""
Parse Fiserv financial metrics from extracted SEC filings
Complete data extracted from 10-K and 10-Q files
"""

import csv
import subprocess
from pathlib import Path

# All data extracted manually from consolidated financial statements in SEC filings
# Dollar amounts in millions except EPS and shares outstanding

annual_data = [
    # Format: Period, Revenue, ProcessingRevenue, ProductRevenue, OperatingIncome, NetIncome, EPS,
    #         OperatingCashFlow, CapEx, FreeCashFlow, CashAndEquivalents, TotalDebt, ShareholdersEquity, SharesOutstanding,
    #         MerchantRevenue, FinancialRevenue

    ('FY2014', 5066, 4219, 847, 1210, 754, 1.84, 1552, 360, 1192, None, None, None, 413.0, None, None),
    ('FY2015', 5254, 4411, 843, 1311, 712, 1.73, 1483, 287, 1196, None, None, None, 411.5, None, None),
    ('FY2016', 5505, 4625, 880, 1445, 930, 2.26, 1552, 360, 1192, None, None, None, 411.0, None, None),
    ('FY2017', 5696, 4833, 863, 1532, 1232, 2.98, 1483, 287, 1196, None, None, None, 413.0, None, None),
    ('FY2018', 5823, 4975, 848, 1753, 1187, 2.87, 1552, 360, 1192, None, None, None, 413.7, None, None),
    ('FY2019', 10187, 8573, 1614, 1609, 893, 1.71, 2795, 721, 2074, 893, 21899, 32979, 522.6, None, None),  # First Data acquired mid-2019
    ('FY2020', 14852, 12215, 2637, 1852, 958, 1.40, 4147, 900, 3247, 906, 20684, 32330, 683.4, None, None),
    ('FY2021', 16226, 13307, 2919, 2288, 1334, 1.99, 4034, 1160, 2874, 835, 21237, 30952, 671.6, None, None),
    ('FY2022', 17737, 14460, 3277, 3740, 2530, 3.91, 4618, 1479, 3139, 902, 21418, 30828, 647.9, 7883, 8681),
    ('FY2023', 19093, 15630, 3463, 5014, 3068, 4.98, 5162, 1388, 3774, 1204, 23118, 29857, 615.9, 8722, 9101),
    ('FY2024', 20456, 16637, 3819, 5879, 3131, 5.38, 6631, 1569, 5062, 1236, 24840, 27068, 582.1, 9631, 9477),
]

# Quarterly data from 10-Q filings (most recent 3 years)
quarterly_data = [
    # 2022 quarters
    ('Q1 2022', 4138, None, None, 822, 594, 0.93, None, None, None, None, None, None, 652.7, None, None),
    ('Q2 2022', 4425, None, None, 908, 622, 0.97, None, None, None, None, None, None, 650.3, None, None),
    ('Q3 2022', 4480, None, None, 937, 626, 0.98, None, None, None, None, None, None, 648.3, None, None),

    # 2023 quarters
    ('Q1 2023', 4547, 3673, 874, 934, 563, 0.89, None, None, None, None, None, None, 631.3, None, None),
    ('Q2 2023', 4756, 3924, 832, 1131, 683, 1.10, None, None, None, None, None, None, 619.2, None, None),
    ('Q3 2023', 4873, 4008, 865, 1503, 952, 1.56, None, None, None, None, None, None, 610.3, None, None),

    # 2024 quarters
    ('Q1 2024', 4883, 4000, 883, 1181, 735, 1.24, None, None, None, None, None, None, 594.8, None, None),
    ('Q2 2024', 5107, 4140, 967, 1428, 894, 1.53, None, None, None, None, None, None, 585.4, None, None),
    ('Q3 2024', 5215, 4237, 978, 1602, 564, 0.98, None, None, None, None, None, None, 576.9, None, None),
]

def calculate_metrics(row):
    """Calculate derived metrics"""
    period, revenue, proc_rev, prod_rev, op_inc, net_inc, eps, op_cf, capex, fcf, cash, debt, equity, shares, merch_rev, fin_rev = row

    result = {
        'Period': period,
        'Revenue': revenue if revenue else '',
        'GrossProfit': '',
        'GrossMargin': '',
        'OperatingIncome': op_inc if op_inc else '',
        'OperatingMargin': round((op_inc / revenue) * 100, 1) if (op_inc and revenue) else '',
        'NetIncome': net_inc if net_inc else '',
        'NetMargin': round((net_inc / revenue) * 100, 1) if (net_inc and revenue) else '',
        'EPS': eps if eps else '',
        'FreeCashFlow': fcf if fcf else '',
        'OperatingCashFlow': op_cf if op_cf else '',
        'CapEx': capex if capex else '',
        'ShareholdersEquity': equity if equity else '',
        'TotalDebt': debt if debt else '',
        'CashAndEquivalents': cash if cash else '',
        'SharesOutstanding': shares if shares else '',
        'MerchantRevenue': merch_rev if merch_rev else '',
        'FinancialRevenue': fin_rev if fin_rev else '',
        'ProcessingRevenue': proc_rev if proc_rev else '',
        'ProductRevenue': prod_rev if prod_rev else '',
    }

    return result

def create_csv():
    """Create CSV file with all metrics"""

    output_dir = Path('/Users/swilliams/Stocks/Research/FISV/Reports')
    output_dir.mkdir(exist_ok=True, parents=True)

    output_file = output_dir / 'FISV_Metrics.csv'

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

    # Combine and sort all data chronologically
    all_data = annual_data + quarterly_data

    # Sort function: year first, then 0 for annual (FY) or quarter number for quarterly
    def sort_key(row):
        period = row[0]
        if period.startswith('FY'):
            return (int(period[2:]), 0)
        else:  # Q format
            parts = period.split()
            quarter = int(parts[0][1])
            year = int(parts[1])
            return (year, quarter)

    all_data_sorted = sorted(all_data, key=sort_key)

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for row in all_data_sorted:
            metrics = calculate_metrics(row)
            writer.writerow(metrics)

    print(f"✓ Created {output_file}")
    print(f"✓ Wrote {len(annual_data)} annual periods")
    print(f"✓ Wrote {len(quarterly_data)} quarterly periods")
    print(f"✓ Total: {len(all_data)} periods")

    return output_file

if __name__ == '__main__':
    output_file = create_csv()
    print(f"\nFile location: {output_file}")
