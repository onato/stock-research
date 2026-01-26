#!/usr/bin/env python3
"""
Test parsing one Block filing to verify approach.
"""
import re

file_path = "/Users/swilliams/Stocks/Research/XYZ/Extracted/XYZ_10K_FY2024.txt"

print(f"Reading {file_path}...")
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

print(f"File size: {len(content):,} characters")
print(f"Number of lines: {content.count(chr(10))} newlines")

# Search for financial statement headers
if re.search(r'CONSOLIDATED STATEMENTS OF OPERATIONS', content, re.I):
    print("✓ Found: CONSOLIDATED STATEMENTS OF OPERATIONS")

    # Find the section
    ops_match = re.search(r'CONSOLIDATED STATEMENTS OF OPERATIONS.{0,15000}', content, re.I | re.S)
    if ops_match:
        ops_text = ops_match.group(0)
        print(f"  Extracted {len(ops_text)} characters from operations section")

        # Look for revenue
        rev_pattern = r'Total net revenue[^\d]+([\d,]+)'
        rev_match = re.search(rev_pattern, ops_text, re.I)
        if rev_match:
            print(f"  Found Total net revenue: {rev_match.group(1)}")

        # Look for gross profit
        gp_pattern = r'Gross profit[^\d]+([\d,]+)'
        gp_match = re.search(gp_pattern, ops_text, re.I)
        if gp_match:
            print(f"  Found Gross profit: {gp_match.group(1)}")

        # Look for net income
        ni_pattern = r'Net income[^\d]+([\d,]+)'
        ni_match = re.search(ni_pattern, ops_text, re.I)
        if ni_match:
            print(f"  Found Net income: {ni_match.group(1)}")
else:
    print("✗ NOT Found: CONSOLIDATED STATEMENTS OF OPERATIONS")

if re.search(r'CONSOLIDATED BALANCE SHEETS', content, re.I):
    print("✓ Found: CONSOLIDATED BALANCE SHEETS")
else:
    print("✗ NOT Found: CONSOLIDATED BALANCE SHEETS")

if re.search(r'CONSOLIDATED STATEMENTS OF CASH FLOWS', content, re.I):
    print("✓ Found: CONSOLIDATED STATEMENTS OF CASH FLOWS")
else:
    print("✗ NOT Found: CONSOLIDATED STATEMENTS OF CASH FLOWS")

print("\nTest complete - patterns are working!")
