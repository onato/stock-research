#!/usr/bin/env python3
"""Download ASX annual and half-year financial reports deterministically.

    python3 scripts/fetch_asx.py TPW.AX --years 2019-2026 [--dry-run]

Why this exists: the ir-scraper agent spent 31 turns (1.7M cached tokens)
on TPW.AX guessing ASX document ids, hitting 403s and the Wayback Machine.
The ASX site is deterministic if you follow its own three hops:

  1. the per-year listing  announcements.do?by=asxCode&asxCode=TPW&timeframe=Y&year=2025
     names every announcement with its idsId and page count;
  2. displayAnnouncement.do?display=pdf&idsId=... is an "agree to terms"
     interstitial whose hidden `pdfURL` field is the real PDF location;
  3. that announcements.asx.com.au/asxpdf/... URL serves the PDF to a
     plain GET.

Filenames follow the repo convention ({T}_Annual_FY2025.pdf,
{T}_HalfYear_H1-FY2026.pdf) with the fiscal label derived from the
announcement date and the fiscal-year-end month in info.json -- the half
to December 2025 of a June filer is H1 FY2026, the period-shift that
mislabelled half-year files by one year before.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import sys
import time
import urllib.request
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parents[1]
BASE = "https://www.asx.com.au"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

ROW_RE = re.compile(
    r"(?P<date>\d{2}/\d{2}/\d{4})<br>.*?"
    r"<td class=\"pricesens\"[^>]*>(?P<sens>.*?)</td>.*?"
    r"idsId=(?P<id>\d+)\">\s*(?P<title>[^<]+?)\s*<br>",
    re.DOTALL)
PAGES_RE = re.compile(r"<span class=\"page\">\s*(\d+)")
ROW_SPLIT = re.compile(r"<tr>", re.IGNORECASE)
PDF_URL_RE = re.compile(r'name="pdfURL"\s+value="([^"]+)"')

ANNUAL_RE = re.compile(r"appendix 4e|annual report|full[- ]year (?:financial )?(?:report|statements)",
                       re.IGNORECASE)
HALF_RE = re.compile(r"appendix 4d|half[- ]?year(?:ly)? (?:financial )?(?:report|statements|accounts)"
                     r"|interim (?:financial )?report", re.IGNORECASE)
NOT_REPORT_RE = re.compile(r"presentation|trading update|notice of|proxy|appendix 4g|appendix 3",
                           re.IGNORECASE)


@dataclass(frozen=True)
class Row:
    date: dt.date
    ids_id: str
    title: str
    pages: int | None
    price_sensitive: bool


@dataclass(frozen=True)
class Pick:
    label: str
    kind: str
    row: Row


def listing_url(code: str, year: int) -> str:
    return (f"{BASE}/asx/v2/statistics/announcements.do"
            f"?by=asxCode&asxCode={code}&timeframe=Y&year={year}")


def parse_listing(page: str) -> list[Row]:
    rows = []
    for chunk in ROW_SPLIT.split(page)[1:]:
        m = ROW_RE.search(chunk)
        if not m:
            continue
        d, mth, y = (int(x) for x in m["date"].split("/"))
        title = html.unescape(re.sub(r"\s+", " ", m["title"])).strip()
        pg = PAGES_RE.search(chunk)
        rows.append(Row(dt.date(y, mth, d), m["id"], title,
                        int(pg.group(1)) if pg else None,
                        "price sensitive" in m["sens"]))
    return rows


def classify(title: str) -> str | None:
    if ANNUAL_RE.search(title) and not re.search(r"notice of|proxy", title, re.IGNORECASE):
        return "Annual"
    if HALF_RE.search(title) and not NOT_REPORT_RE.search(title):
        return "HalfYear"
    return None


def fiscal_label(date: dt.date, kind: str, fy_end_month: int) -> str:
    """Label of the most recent period of `kind` ending before `date`."""
    half_end_month = (fy_end_month + 6 - 1) % 12 + 1
    if kind == "Annual":
        fy = date.year if date.month > fy_end_month else date.year - 1
        return f"FY{fy}"
    # most recent half-year end before the announcement
    end_year = date.year if date.month > half_end_month else date.year - 1
    fy = end_year + 1 if half_end_month > fy_end_month else end_year
    return f"H1-FY{fy}"


def filename(ticker: str, label: str, kind: str) -> str:
    return f"{ticker}_{kind}_{label}.pdf"


def select(rows: list[Row], fy_end_month: int) -> list[Pick]:
    """One document per fiscal label: the longest candidate wins (the
    28-page accounts over the 3-page results release)."""
    best: dict[str, Pick] = {}
    for r in rows:
        kind = classify(r.title)
        if not kind:
            continue
        label = fiscal_label(r.date, kind, fy_end_month)
        cur = best.get(label)
        if cur is None or (r.pages or 0) > (cur.row.pages or 0):
            best[label] = Pick(label, kind, r)
    return sorted(best.values(), key=lambda p: p.row.date)


def missing(picks: list[Pick], ticker: str, out_dir: pathlib.Path) -> list[Pick]:
    return [p for p in picks if not (out_dir / filename(ticker, p.label, p.kind)).exists()]


def pdf_url(interstitial: str) -> str | None:
    m = PDF_URL_RE.search(interstitial)
    return html.unescape(m.group(1)) if m else None


def parse_years(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def get(url: str, referer: str | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Referer": referer} if referer else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fy_end_month_for(ticker: str, repo: pathlib.Path = REPO) -> int:
    info = repo / "research" / ticker / "info.json"
    if info.exists():
        fye = json.loads(info.read_text()).get("fiscal_year_end")
        if fye:
            return int(str(fye).split("-")[0])
    return 6   # the ASX default


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ticker", help="e.g. TPW.AX")
    ap.add_argument("--years", required=True, help="2019-2026 or 2025")
    ap.add_argument("--fy-end", type=int, help="fiscal year-end month (default: info.json, else 6)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=pathlib.Path, help="override research/{T}/PDFs")
    a = ap.parse_args(argv)
    ticker = a.ticker.upper()
    code = ticker.split(".")[0]
    fy_end = a.fy_end or fy_end_month_for(ticker)
    out_dir = a.out or REPO / "research" / ticker / "PDFs"

    rows: list[Row] = []
    for y in parse_years(a.years):
        rows += parse_listing(get(listing_url(code, y)).decode("utf-8", "replace"))
        time.sleep(0.5)
    picks = select(rows, fy_end)
    todo = missing(picks, ticker, out_dir)
    print(f"{ticker}: {len(rows)} announcements, {len(picks)} reports, {len(todo)} to fetch")
    for p in todo:
        name = filename(ticker, p.label, p.kind)
        print(f"  {name:36s} <- {p.row.date} idsId={p.row.ids_id} {p.row.pages or '?'}p  {p.row.title}")
        if a.dry_run:
            continue
        inter = f"{BASE}/asx/v2/statistics/displayAnnouncement.do?display=pdf&idsId={p.row.ids_id}"
        url = pdf_url(get(inter).decode("utf-8", "replace"))
        if not url:
            print("     !! no pdfURL on the interstitial", file=sys.stderr)
            continue
        data = get(url, referer=inter)
        if not data.startswith(b"%PDF"):
            print(f"     !! not a PDF ({len(data)} bytes)", file=sys.stderr)
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / name).write_bytes(data)
        print(f"     saved {len(data):,} bytes")
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
