#!/usr/bin/env python3
"""Deterministic annuals adapter backed by AnnualReports.com's hosted archive.

URL pattern (verified 2026-08-06):
  https://www.annualreports.com/HostedData/AnnualReportArchive/{initial}/{EXCH}_{CODE}_{YEAR}.pdf
where {initial} is the first letter of the company name and EXCH is LSE/ASX/NZE.

Annuals only — interims/quarterlies still need other routes. gate.py validates
everything afterwards as usual.

Usage: annualreports.py BNZL.L --dest DIR [--after-year 2021]
Exit 0 = ran cleanly (downloads or none); nonzero = adapter not applicable/failed.
"""
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
BASE = "https://www.annualreports.com/HostedData/AnnualReportArchive"
EXCH = {"L": "LSE", "AX": "ASX", "NZ": "NZE"}
SEED_YEARS = 8
REPO = Path(__file__).resolve().parents[3]


def head_ok(url: str) -> bool:
    code = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-I", "-L",
         "--max-time", "20", url, "-A", UA],
        capture_output=True, text=True, timeout=30).stdout.strip()
    return code == "200"


def main() -> int:
    ticker = sys.argv[1]
    args = sys.argv[2:]
    dest = Path(args[args.index("--dest") + 1])
    after = int(args[args.index("--after-year") + 1]) if "--after-year" in args else 0

    code, _, suffix = ticker.partition(".")
    exch = EXCH.get(suffix)
    if not exch:
        print(f"annualreports-adapter: no exchange mapping for .{suffix}")
        return 1

    companies = json.loads((REPO / "state" / "companies.json").read_text())
    name = companies.get(ticker, {}).get("name", "")
    m = re.search(r"[a-z]", name.lower())
    if not m:
        print(f"annualreports-adapter: no company name for {ticker} — cannot derive archive letter")
        return 1
    initial = m.group(0)

    this_year = datetime.date.today().year
    floor = max(after, this_year - SEED_YEARS) if not after else after
    got = 0
    for year in range(this_year, floor, -1):
        url = f"{BASE}/{initial}/{exch}_{code}_{year}.pdf"
        if not head_ok(url):
            continue
        out = dest / f"{ticker}_Annual_FY{year}.pdf"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["curl", "-sfL", "--max-time", "300", url, "-A", UA,
                            "-o", str(out)], timeout=320, check=True)
            print(f"annualreports-adapter: downloaded {out.name}")
            got += 1
        except Exception as e:
            print(f"annualreports-adapter: FAILED {out.name}: {e}")
            out.unlink(missing_ok=True)
    print(f"annualreports-adapter: {got} annuals for {ticker} (years > {floor})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
