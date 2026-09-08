#!/usr/bin/env python3
"""Deterministic HKEXnews filing adapter.

Resolves the ticker on HKEXnews, lists annual (t2code 40100) and interim
(40200) reports plus the quarterlies that live outside that category, and
plans everything newer than --after-year under the repo naming convention.

The listing categories are not clean: ESG/sustainability reports, "printed
version" reissues and summaries share them with the real reports, so the
plan filters on the title. Rows arrive date-sorted, so the first posting for
a period is the one kept.

filing_gate still validates every download afterwards.

    python3 scripts/adapters/hkex.py 1211.HK --dest DIR [--after-year 2023]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from adapters import (
    DEFAULT_DEADLINE,
    Deadline,
    curl_download,
    curl_text,
    is_pdf,
)

BASE = "https://www1.hkexnews.hk"
SEARCH = f"{BASE}/search/titleSearchServlet.do"

# Seeding a ticker from nothing: how far back to go before the volume stops
# paying for itself. An update run (after_year > 0) is never capped.
SEED_ANNUALS = 8
SEED_INTERIMS = 2
SEED_QUARTERLIES = 4

QUARTER_RE = re.compile(r"(first|second|third|fourth) quarterly report")
QUARTER_MAP = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
# Same category as the reports, but not reports.
EXCLUDE = ("printed version", "summary", "sustainability", "esg")


# --- network I/O (monkeypatched in tests) ----------------------------------

def get(url: str) -> str:
    return curl_text(url)


def stock_id(code: str) -> int:
    """HKEXnews' internal id for a listing code."""
    raw = get(f"{BASE}/search/prefix.do?callback=cb&lang=EN&type=A"
              f"&name={code}&market=SEHK")
    info = json.loads(raw[raw.index("(") + 1:raw.rindex(")")])["stockInfo"]
    matches = [s for s in info if s["code"].lstrip("0") == code.lstrip("0")]
    if not matches:
        raise LookupError(f"no HKEX stock for code {code}")
    return int(matches[0]["stockId"])


def _search(**params: str | int) -> list[dict[str, Any]]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    raw = get(f"{SEARCH}?{query}")
    d = json.loads(raw)
    result = d.get("result", [])
    return json.loads(result) if isinstance(result, str) else result


def filings(sid: int, t2code: str) -> list[dict[str, Any]]:
    """Financial-report category listing (40100 annual, 40200 interim)."""
    return _search(sortDir=0, sortByOptions="DateTime", category=0, market="SEHK",
                   stockId=sid, documentType=-1, fromDate=20150101, toDate=20991231,
                   title="", searchType=1, t1code=40000, t2Gcode=-2,
                   t2code=t2code, rowRange=200, lang="E")


def quarterly_filings(sid: int) -> list[dict[str, Any]]:
    """Quarterly reports live outside t1=40000 -- find them by title search."""
    return _search(sortDir=0, sortByOptions="DateTime", category=0, market="SEHK",
                   stockId=sid, documentType=-1, fromDate=20150101, toDate=20991231,
                   title="quarterly", searchType=1, t1code=-2, t2Gcode=-2,
                   t2code=-2, rowRange=200, lang="E")


def download(url: str, out: Path, deadline: Deadline | None = None) -> None:
    curl_download(url, out, deadline)


# --- pure functions --------------------------------------------------------

def title_year(title: str) -> int:
    """The fiscal year a report title names.

    "2024/25 Annual Report" and "Annual Report 2024-25" both mean fiscal
    2025: the split year's second half is the one that counts.
    """
    years = [int(y) for y in re.findall(r"(20\d\d)", title)]
    frac = re.search(r"20(\d\d)\s*[/\-–—]\s*(\d\d)", title)
    if frac:
        years.append(2000 + int(frac[2]))
    return max(years, default=0)


def _is_report(title: str, wanted: str) -> bool:
    lowered = title.lower()
    if wanted not in lowered:
        return False
    return not any(w in lowered for w in EXCLUDE)


def plan_from_filings(annual_rows: list[dict[str, Any]],
                      interim_rows: list[dict[str, Any]],
                      quarterly_rows: list[dict[str, Any]],
                      after_year: int, ticker: str) -> list[tuple[str, str, int]]:
    """(filename, url, year) for every report worth fetching, newest first.

    `after_year` is an exclusive floor; 0 means seeding, which caps the
    volume per kind rather than pulling a company's entire history.
    """
    plan: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()

    def consider(kind: str, period: str, row: dict[str, Any], year: int) -> None:
        if (kind, period) in seen:
            return          # date-sorted: keep the newest posting only
        seen.add((kind, period))
        plan.append((f"{ticker}_{kind}_{period}.pdf", BASE + row["FILE_LINK"], year))

    for rows, kind, wanted in ((annual_rows, "Annual", "annual report"),
                               (interim_rows, "HalfYear", "interim report")):
        for row in rows:
            title = row.get("TITLE", "")
            if not _is_report(title, wanted):
                continue
            year = title_year(title)
            if not year or (after_year and year <= after_year):
                continue
            consider(kind, f"FY{year}" if kind == "Annual" else f"H1-{year}", row, year)

    for row in quarterly_rows:
        title = row.get("TITLE", "")
        m = QUARTER_RE.search(title.lower())
        year = title_year(title)
        if not m or not year or (after_year and year <= after_year):
            continue
        consider("Quarterly", f"{QUARTER_MAP[m[1]]}-{year}", row, year)

    if not after_year:
        by_kind = [
            ([p for p in plan if "_Annual_" in p[0]], SEED_ANNUALS),
            ([p for p in plan if "_HalfYear_" in p[0]], SEED_INTERIMS),
            ([p for p in plan if "_Quarterly_" in p[0]], SEED_QUARTERLIES),
        ]
        plan = [p for rows, cap in by_kind for p in rows[:cap]]

    return sorted(plan, key=lambda p: -p[2])


# --- planning with I/O -----------------------------------------------------

def plan(ticker: str, after_year: int,
         deadline: Deadline | None = None) -> list[tuple[str, str, int]]:
    code = ticker.split(".")[0]
    sid = stock_id(code)
    if deadline is not None:
        deadline.check("the HKEX listings")
    return plan_from_filings(filings(sid, "40100"), filings(sid, "40200"),
                             quarterly_filings(sid), after_year, ticker)


def fetch(ticker: str, dest: Path, after_year: int,
          deadline_s: float = DEFAULT_DEADLINE) -> int:
    """Download every planned report into `dest`; returns files written."""
    deadline = Deadline(deadline_s)
    deadline.check("the HKEX listings")
    todo = plan(ticker, after_year, deadline)
    if not todo:
        print(f"hkex: nothing newer than {after_year} for {ticker}")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    ok = 0
    for name, url, _year in todo:
        deadline.check(name)
        out = dest / name
        try:
            download(url, out, deadline)
        except (subprocess.SubprocessError, OSError) as e:
            print(f"hkex: FAILED {name}: {e}")
            out.unlink(missing_ok=True)
            continue
        if not is_pdf(out):
            out.unlink(missing_ok=True)
            print(f"hkex: skipped non-PDF payload for {name}")
            continue
        print(f"hkex: downloaded {name}")
        ok += 1
    print(f"hkex: {ok}/{len(todo)} downloaded")
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
        print(f"hkex: {e}")
        return 3
    except (LookupError, RuntimeError, subprocess.SubprocessError,
            json.JSONDecodeError, OSError) as e:
        print(f"hkex: adapter failed ({e})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
