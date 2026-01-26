#!/usr/bin/env python3
"""
Extract a sample section from XBRL to understand structure.
"""

from pathlib import Path

filepath = Path('/Users/swilliams/Stocks/Research/UBER/Extracted/UBER_10Q_Q3-2024.txt')

with open(filepath, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if i == 8:  # The long line with XBRL data
            # Find sections related to Revenue
            revenue_sections = []
            pos = 0
            while True:
                pos = line.find('Revenues', pos)
                if pos == -1:
                    break
                # Get context around this match
                start = max(0, pos - 500)
                end = min(len(line), pos + 500)
                revenue_sections.append(line[start:end])
                pos += 1

            print(f"Found {len(revenue_sections)} sections with 'Revenues'")
            print("\nFirst 3 sections:")
            for i, section in enumerate(revenue_sections[:3], 1):
                print(f"\n--- Section {i} ---")
                print(section)
                print()

            # Also look for Gross Bookings
            print("\n" + "="*60)
            gb_sections = []
            pos = 0
            while True:
                pos = line.lower().find('gross bookings', pos)
                if pos == -1:
                    break
                start = max(0, pos - 300)
                end = min(len(line), pos + 300)
                gb_sections.append(line[start:end])
                pos += 1

            print(f"Found {len(gb_sections)} sections with 'Gross Bookings'")
            if gb_sections:
                print("\nFirst section:")
                print(gb_sections[0])

            break
