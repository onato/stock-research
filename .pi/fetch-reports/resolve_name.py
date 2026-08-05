#!/usr/bin/env python3
"""Ensure state/companies.json knows this ticker's company name.

Resolves via Yahoo's chart API when missing and persists the result, so the
fetch prompt can name the actual company (LSE codes like BNZL are not
guessable) and gate.py's company-name check is armed for the same run.

Usage: resolve_name.py TICKER   -> prints the name (or the ticker if unknown)
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPANIES = REPO / "state" / "companies.json"

ticker = sys.argv[1]
companies = json.loads(COMPANIES.read_text())
entry = companies.get(ticker, {})
name = entry.get("name", "")

if not name or name == ticker:
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", "10",
             f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
             "-H", "User-Agent: Mozilla/5.0"],
            capture_output=True, text=True, timeout=15).stdout
        meta = json.loads(out)["chart"]["result"][0]["meta"]
        name = meta.get("longName") or meta.get("shortName") or ""
    except Exception:
        name = ""
    if name:
        companies[ticker] = {"name": name, "sector": entry.get("sector", "Unknown")}
        COMPANIES.write_text(json.dumps(companies, indent=2, sort_keys=True) + "\n")

print(name or ticker)
