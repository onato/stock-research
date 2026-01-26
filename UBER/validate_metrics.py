#!/usr/bin/env python3
"""
Validate the UBER_Metrics.csv file for consistency and calculations.
"""

import csv
from pathlib import Path

def validate_metrics():
    csv_path = Path('/Users/swilliams/Stocks/Research/UBER/Reports/UBER_Metrics.csv')

    print("UBER METRICS VALIDATION")
    print("=" * 70)
    print()

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Total periods: {len(rows)}")
    print()

    # Validate calculations
    print("Validating GrossProfit and GrossMargin calculations:")
    print("-" * 70)

    errors = []
    for row in rows:
        period = row['Period']

        # Check Gross Profit calculation
        if row['Revenue'] and row['CostOfRevenue'] and row['GrossProfit']:
            revenue = float(row['Revenue'])
            cost = float(row['CostOfRevenue'])
            gross_profit = float(row['GrossProfit'])

            calculated_gp = revenue - cost

            if abs(calculated_gp - gross_profit) > 1:  # Allow 1M rounding difference
                errors.append(f"{period}: GrossProfit mismatch - Reported: {gross_profit}, Calculated: {calculated_gp:.1f}")

        # Check Gross Margin calculation
        if row['Revenue'] and row['GrossProfit'] and row['GrossMargin']:
            revenue = float(row['Revenue'])
            gross_profit = float(row['GrossProfit'])
            gross_margin = float(row['GrossMargin'])

            calculated_gm = (gross_profit / revenue) * 100

            if abs(calculated_gm - gross_margin) > 0.2:  # Allow 0.2% difference
                errors.append(f"{period}: GrossMargin mismatch - Reported: {gross_margin}%, Calculated: {calculated_gm:.1f}%")

    if errors:
        print("ERRORS FOUND:")
        for error in errors:
            print(f"  ✗ {error}")
    else:
        print("✓ All calculations verified successfully!")

    print()
    print("=" * 70)
    print()

    # Summary statistics
    print("KEY METRICS SUMMARY:")
    print("-" * 70)

    # Most recent quarter
    latest = rows[-1]
    print(f"\nMost Recent Period: {latest['Period']}")
    print(f"  Revenue:            ${latest['Revenue']}M")
    print(f"  Gross Profit:       ${latest['GrossProfit']}M")
    print(f"  Gross Margin:       {latest['GrossMargin']}%")
    print(f"  Net Income:         ${latest['NetIncome']}M")
    print(f"  EPS:                ${latest['EPS']}")
    print(f"  Free Cash Flow:     ${latest['FreeCashFlow']}M")
    print(f"  Gross Bookings:     ${latest['GrossBookings']}M")
    print(f"  MAPCs:              {latest['MAPCs']}M")
    print(f"  Trips:              {latest['Trips']}M")

    print(f"\nBalance Sheet (as of {latest['Period']}):")
    print(f"  Shareholders' Equity: ${latest['ShareholdersEquity']}M")
    print(f"  Total Debt:           ${latest['TotalDebt']}M")
    print(f"  Cash & Equivalents:   ${latest['CashAndEquivalents']}M")

    if latest['TotalDebt'] and latest['CashAndEquivalents']:
        net_debt = float(latest['TotalDebt']) - float(latest['CashAndEquivalents'])
        print(f"  Net Debt:             ${net_debt:.1f}M")

    if latest['ShareholdersEquity'] and latest['SharesOutstanding']:
        book_value_per_share = float(latest['ShareholdersEquity']) / float(latest['SharesOutstanding'])
        print(f"  Book Value/Share:     ${book_value_per_share:.2f}")

    print()

    # Growth metrics
    print("GROWTH METRICS:")
    print("-" * 70)

    # Compare Q1 2020 vs Q3 2024
    first_q = rows[0]
    latest_q = rows[-2]  # Q3 2024 (before FY2024)

    if first_q['Revenue'] and latest_q['Revenue']:
        first_rev = float(first_q['Revenue'])
        latest_rev = float(latest_q['Revenue'])
        growth = ((latest_rev / first_rev) - 1) * 100
        print(f"\nQuarterly Revenue Growth ({first_q['Period']} → {latest_q['Period']}):")
        print(f"  ${first_rev}M → ${latest_rev}M ({growth:.1f}% total growth)")

    # Annual comparison
    fy2020 = next(r for r in rows if r['Period'] == 'FY2020')
    fy2024 = next(r for r in rows if r['Period'] == 'FY2024')

    if fy2020['Revenue'] and fy2024['Revenue']:
        fy20_rev = float(fy2020['Revenue'])
        fy24_rev = float(fy2024['Revenue'])
        annual_cagr = ((fy24_rev / fy20_rev) ** (1/4) - 1) * 100
        total_growth = ((fy24_rev / fy20_rev) - 1) * 100
        print(f"\nAnnual Revenue (FY2020 → FY2024):")
        print(f"  ${fy20_rev}M → ${fy24_rev}M")
        print(f"  Total Growth: {total_growth:.1f}%")
        print(f"  CAGR: {annual_cagr:.1f}%")

    # Profitability progression
    print(f"\nNet Income Progression:")
    print(f"  FY2020: ${fy2020['NetIncome']}M (loss)")
    print(f"  FY2024: ${fy2024['NetIncome']}M (profit)")

    if fy2020['NetIncome'] and fy2024['NetIncome']:
        improvement = float(fy2024['NetIncome']) - float(fy2020['NetIncome'])
        print(f"  Improvement: ${improvement:.0f}M")

    print()
    print("=" * 70)
    print()

    # Data completeness
    print("DATA COMPLETENESS CHECK:")
    print("-" * 70)

    fields = [
        'Revenue', 'CostOfRevenue', 'GrossProfit', 'NetIncome', 'EPS',
        'GrossBookings', 'MAPCs', 'Trips', 'ShareholdersEquity', 'TotalDebt',
        'CashAndEquivalents', 'FreeCashFlow'
    ]

    for field in fields:
        count = sum(1 for row in rows if row[field])
        percentage = (count / len(rows)) * 100
        status = "✓" if percentage > 80 else "⚠" if percentage > 50 else "✗"
        print(f"  {status} {field:20} {count:2}/{len(rows)} periods ({percentage:5.1f}%)")

    print()
    print("=" * 70)

if __name__ == '__main__':
    validate_metrics()
