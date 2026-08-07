#!/usr/bin/env python3
"""Deterministic ASX filing adapter — no model involved.

The Markit JSON API only serves a ~30-day window, so this uses ASX's legacy
per-year announcements pages (full history), follows each document's
agreement interstitial to the real announcements.asx.com.au PDF, and
downloads annual / half-year reports. Period labels prefer a 4-digit year in
the headline, else the listing year — gate.py's period check arbitrates.

Exit 0 = ran cleanly; nonzero = adapter failed (caller falls back to model).

Usage: asx.py BHP.AX --dest DIR [--after-year 2023]
"""
import datetime
import re
import subprocess
import sys
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
LIST = "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode={code}&timeframe=Y&year={year}"
VIEW = "https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do?display=pdf&idsId={ids}"
SEED_ANNUALS = 8
SEED_HALVES = 2

ANNUAL_RE = re.compile(r"\bannual report\b", re.I)
HALF_RE = re.compile(r"\b(half[ -]?year(ly)?|interim) (financial )?(report|accounts|results)\b", re.I)
EXCLUDE_RE = re.compile(r"\b(sustainab|esg|corporate governance|remuneration|"
                        r"presentation|webcast|transcript|notice|agm|concise)\b", re.I)


def get(url: str) -> str:
    return subprocess.run(
        ["curl", "-sfL", "--max-time", "30", url, "-A", UA],
        capture_output=True, text=True, timeout=40, check=True).stdout


def rows(code: str, year: int):
    try:
        html = get(LIST.format(code=code, year=year))
    except Exception:
        return
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        ids = re.search(r"idsId=(\d+)", tr)
        if not ids:
            continue
        text = re.sub(r"<[^>]+>", " ", tr)
        text = re.sub(r"\s+", " ", text).strip()
        yield ids[1], text


def pdf_url(ids: str) -> str:
    page = get(VIEW.format(ids=ids))
    m = re.search(r"https?://announcements\.asx\.com\.au/asxpdf/[^\"']+\.pdf", page)
    if not m:
        raise LookupError(f"no asxpdf link behind idsId={ids}")
    return m[0]


def main() -> int:
    ticker = sys.argv[1]
    args = sys.argv[2:]
    dest = Path(args[args.index("--dest") + 1])
    after = int(args[args.index("--after-year") + 1]) if "--after-year" in args else 0
    code = ticker.split(".")[0]

    this_year = datetime.date.today().year
    floor = after if after else this_year - SEED_ANNUALS
    plan = []  # (filename, idsId, year)
    seen = {}
    for year in range(this_year, floor, -1):
        for ids, text in rows(code, year) or []:
            if EXCLUDE_RE.search(text):
                continue
            if ANNUAL_RE.search(text):
                typ = "Annual"
            elif HALF_RE.search(text):
                typ = "HalfYear"
            else:
                continue
            m = re.search(r"(20\d\d)", text[20:])  # skip the dd/mm/yyyy date cell
            label_year = int(m[1]) if m else year
            if after and label_year <= after:
                continue
            pm = re.search(r"(\d+) pages?", text)
            pages = int(pm[1]) if pm else 0
            if pages and pages < (10 if typ == "Annual" else 4):
                continue  # lodgement/correction cover letters, not the report
            period = f"FY{label_year}" if typ == "Annual" else f"H1-{label_year}"
            key = (typ, period)
            # keep the fattest candidate per period: the real report beats the
            # same-day cover letter (WOR FY2025: 1-page correction vs 152 pages)
            if key in seen and seen[key][1] >= pages:
                continue
            seen[key] = (ids, pages, label_year)

    plan = [(f"{ticker}_{typ}_{period}.pdf", ids, y)
            for (typ, period), (ids, _pages, y) in seen.items()]
    if not after:
        annuals = [p for p in plan if "_Annual_" in p[0]][:SEED_ANNUALS]
        halves = [p for p in plan if "_HalfYear_" in p[0]][:SEED_HALVES]
        plan = annuals + halves

    if not plan:
        print(f"asx-adapter: nothing newer than {after} for {ticker}")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    ok = 0
    for name, ids, _ in sorted(plan, key=lambda p: -p[2]):
        out = dest / name
        try:
            url = pdf_url(ids)
            subprocess.run(["curl", "-sfL", "--max-time", "300", url, "-A", UA,
                            "-o", str(out)], timeout=320, check=True)
            print(f"asx-adapter: downloaded {name}")
            ok += 1
        except Exception as e:
            print(f"asx-adapter: FAILED {name}: {e}")
            out.unlink(missing_ok=True)
    print(f"asx-adapter: {ok}/{len(plan)} downloaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
