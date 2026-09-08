#!/usr/bin/env python3
"""Deterministic annuals adapter backed by AnnualReports.com's hosted archive.

URL pattern (verified 2026-08-06):

    https://www.annualreports.com/HostedData/AnnualReportArchive/{initial}/{EXCH}_{CODE}_{YEAR}.pdf

where {initial} is the first letter of the company name and EXCH is
LSE/ASX/NZE. That makes the whole candidate list derivable: the only network
calls are the HEAD that says which candidates exist and the download itself.

Annuals only -- interims and quarterlies still need the exchange adapters.
This is the seed path for a ticker with almost nothing on file, never the
update path. filing_gate validates everything afterwards as usual.

    python3 scripts/adapters/annualreports.py BNZL.L --dest DIR [--after-year 2021]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

from adapters import (
    DEFAULT_DEADLINE,
    HEAD_TIMEOUT,
    UA,
    Deadline,
    curl_download,
    is_pdf,
)

REPO = Path(__file__).resolve().parents[2]
BASE = "https://www.annualreports.com/HostedData/AnnualReportArchive"
# The archive's own exchange codes. .HK and US listings are not in it.
EXCH = {"L": "LSE", "AX": "ASX", "NZ": "NZE"}
SEED_YEARS = 8


# --- network I/O (monkeypatched in tests) ----------------------------------

def head_ok(url: str) -> bool:
    """Does the archive actually hold this candidate?"""
    code = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-I", "-L",
         "--max-time", str(HEAD_TIMEOUT), url, "-A", UA],
        capture_output=True, text=True, timeout=HEAD_TIMEOUT + 10,
        check=False).stdout.strip()
    return code == "200"


def download(url: str, out: Path, deadline: Deadline | None = None) -> None:
    curl_download(url, out, deadline)


# --- pure functions --------------------------------------------------------

def archive_initial(company_name: str) -> str | None:
    """The archive shelves a company under the first *letter* of its name --
    "3i Group" lives under 'i', not '3'."""
    m = re.search(r"[a-z]", company_name.lower())
    return m.group(0) if m else None


def candidate_urls(ticker: str, company_name: str, after_year: int,
                   today: dt.date) -> list[tuple[int, str, str]]:
    """(year, url, filename) for every archive slot worth a HEAD, newest first.

    `after_year` is an exclusive floor; 0 means seeding, which walks back
    SEED_YEARS from today.
    """
    code, _, suffix = ticker.partition(".")
    exch = EXCH.get(suffix)
    if not exch:
        return []
    initial = archive_initial(company_name)
    if not initial:
        return []
    floor = after_year or today.year - SEED_YEARS
    return [(y, f"{BASE}/{initial}/{exch}_{code}_{y}.pdf",
             f"{ticker}_Annual_FY{y}.pdf")
            for y in range(today.year, floor, -1)]


def company_name_for(ticker: str, repo: Path = REPO) -> str:
    companies = repo / "state" / "companies.json"
    if not companies.exists():
        return ""
    try:
        entry = json.loads(companies.read_text()).get(ticker) or {}
    except json.JSONDecodeError:
        return ""
    return str(entry.get("name", ""))


# --- planning with I/O -----------------------------------------------------

def fetch(ticker: str, dest: Path, after_year: int, company_name: str | None = None,
          today: dt.date | None = None, repo: Path = REPO,
          deadline_s: float = DEFAULT_DEADLINE) -> int:
    """Download every archive annual that exists into `dest`; returns files written."""
    deadline = Deadline(deadline_s)
    deadline.check("the archive lookup")
    name = company_name if company_name is not None else company_name_for(ticker, repo)
    candidates = candidate_urls(ticker, name, after_year, today or dt.date.today())
    if not candidates:
        print(f"annualreports: no archive route for {ticker}")
        return 0

    ok = 0
    for _year, url, filename in candidates:
        deadline.check(filename)
        if not head_ok(url):
            continue
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / filename
        try:
            download(url, out, deadline)
        except (subprocess.SubprocessError, OSError) as e:
            print(f"annualreports: FAILED {filename}: {e}")
            out.unlink(missing_ok=True)
            continue
        if not is_pdf(out):
            out.unlink(missing_ok=True)
            print(f"annualreports: skipped non-PDF payload for {filename}")
            continue
        print(f"annualreports: downloaded {filename}")
        ok += 1
    print(f"annualreports: {ok} annuals for {ticker}")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ticker")
    ap.add_argument("--dest", type=Path, required=True)
    ap.add_argument("--after-year", type=int, default=0)
    ap.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    a = ap.parse_args(argv)
    try:
        fetch(a.ticker, a.dest, a.after_year, deadline_s=a.deadline)
    except TimeoutError as e:
        print(f"annualreports: {e}")
        return 3
    except (RuntimeError, subprocess.SubprocessError, OSError) as e:
        print(f"annualreports: adapter failed ({e})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
