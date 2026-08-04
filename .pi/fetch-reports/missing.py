#!/usr/bin/env python3
"""Shopping-list builder: report what filings exist for a ticker and what's newest.

Deterministic — the model never decides what we have, only goes hunting for
anything newer than what this script reports.

Usage: missing.py TICKER [--json]
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NAME_RE = re.compile(
    r"^(?P<ticker>.+?)_(?P<type>Annual|HalfYear|Quarterly|Presentation|Results)_(?P<period>[A-Za-z0-9-]+)\.pdf$"
)


def period_year(period: str) -> int:
    years = re.findall(r"(\d{4})", period)
    return max((int(y) for y in years), default=0)


def scan(ticker: str) -> dict:
    pdf_dir = REPO / "research" / ticker / "PDFs"
    companies = json.loads((REPO / "state" / "companies.json").read_text())
    info = companies.get(ticker, {})
    filings: dict[str, list] = {}
    unrecognized = []
    for f in sorted(pdf_dir.glob("*.pdf")) if pdf_dir.is_dir() else []:
        m = NAME_RE.match(f.name)
        if not m:
            unrecognized.append(f.name)
            continue
        filings.setdefault(m["type"], []).append(m["period"])
    newest = {
        t: max(ps, key=lambda p: (period_year(p), p)) for t, ps in filings.items()
    }
    return {
        "ticker": ticker,
        "company": info.get("name", ticker),
        "sector": info.get("sector", ""),
        "pdf_dir": str(pdf_dir),
        "filings": filings,
        "counts": {t: len(ps) for t, ps in filings.items()},
        "newest": newest,
        "newest_year": max((period_year(p) for p in newest.values()), default=0),
        "unrecognized": unrecognized,
    }


if __name__ == "__main__":
    ticker = sys.argv[1]
    result = scan(ticker)
    if "--json" in sys.argv:
        print(json.dumps(result, indent=1))
    else:
        print(f"{result['company']} ({ticker})")
        for t, p in sorted(result["newest"].items()):
            print(f"  newest {t}: {p}  ({result['counts'][t]} on file)")
        if not result["newest"]:
            print("  no filings on file")
