#!/usr/bin/env python3
"""Quick test of parsing logic"""
import re
from pathlib import Path

def clean_number(text):
    if not text or text in ['—', '-', '', '$']:
        return None
    text = text.replace('$', '').replace(',', '').strip()
    is_negative = text.startswith('(') and text.endswith(')')
    if is_negative:
        text = text[1:-1].strip()
    try:
        value = float(text)
        return -value if is_negative else value
    except:
        return None

# Test on FY2025
filepath = Path("/Users/swilliams/Stocks/Research/NVDA/Extracted/NVDA_10K_FY2025.txt")
with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

print("Testing FY2025 extraction:")
print("="*50)

# Find revenue
rev_match = re.search(r'Revenue\s+\$\s*([\d,]+)', content)
if rev_match:
    print(f"Revenue: ${clean_number(rev_match.group(1))}M")

# Find net income
ni_match = re.search(r'Net income\s+\$\s*([\d,]+)', content)
if ni_match:
    print(f"Net Income: ${clean_number(ni_match.group(1))}M")

# Find compute revenue
comp_match = re.search(r'Compute\s*&\s*Networking\s+\$?\s*([\d,]+)', content, re.I)
if comp_match:
    print(f"Compute & Networking: ${clean_number(comp_match.group(1))}M")

# Find graphics revenue
graph_match = re.search(r'Graphics\s+\$?\s*([\d,]+)', content, re.I)
if graph_match:
    print(f"Graphics: ${clean_number(graph_match.group(1))}M")

print("\n✓ Basic extraction working!")
