#!/usr/bin/env python3
"""Deterministic NZX adapter -- updates and full seeding.

nzx.com is Next.js: the company announcements page hydrates the CURRENT
year's announcements (typed ANNREP/HALFYR/FLLYR/...) into __NEXT_DATA__, and
attachment PDFs download from a public api.nzx.com URL. Back-years come from
the per-year listing the site's own client uses,

    api.nzx.com/public/company/{CompanyID}/announcements/{YEAR}/all.json

where CompanyID (e.g. FPH000000) is read from the page hydration -- it is NOT
always the ticker code: AFC.NZ is IRG000000.

Seed mode (after_year=0) walks the last BACK_YEARS of listings; periods
already held in research/{TICKER}/ are skipped, so seeding and thin-holdings
backfill are the same call and a re-run is idempotent.

Fattest-attachment rule (the ASX cover-letter lesson): when an announcement
carries several PDFs, take the largest by Content-Length.

    python3 scripts/adapters/nzx.py SMI.NZ --dest DIR --after-year 2025
"""

from __future__ import annotations

import argparse
import datetime as dt
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
    curl_head_size,
    curl_text,
    is_pdf,
)

REPO = Path(__file__).resolve().parents[2]
BACK_YEARS = 8
API = "https://api.nzx.com/public"
SITE = "https://www.nzx.com"

# Small NZX companies often never lodge a glossy ANNREP -- the FLLYR results
# announcement, with the financial statements attached, IS the annual filing.
TYPES = {"ANNREP": "Annual", "FLLYR": "Annual",
         "HALFYR": "HalfYear", "INTERIM": "HalfYear"}

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)
PDF_RE = re.compile(r"https?://api\.nzx\.com/public/announcement/[^\"\\s]+?\.pdf")


# --- network I/O (monkeypatched in tests) ----------------------------------

def get(url: str) -> str:
    return curl_text(url)


def next_data(url: str) -> dict[str, Any]:
    """The __NEXT_DATA__ hydration blob of a nzx.com page."""
    m = NEXT_DATA_RE.search(get(url))
    return json.loads(m[1]) if m else {}


def head_size(url: str) -> int:
    return curl_head_size(url)


def download(url: str, out: Path, deadline: Deadline | None = None) -> None:
    curl_download(url, out, deadline)


# --- pure functions --------------------------------------------------------

def _queries(hydration: dict[str, Any]) -> list[dict[str, Any]]:
    return (hydration.get("props", {}).get("pageProps", {})
            .get("dehydratedState", {}).get("queries", []) or [])


def company_id_from_hydration(hydration: dict[str, Any]) -> str | None:
    """The CompanyID the per-year listing API is keyed by, from the
    announcements query key. Not always the ticker code."""
    for q in _queries(hydration):
        key = q.get("queryKey") or [None]
        if key[0] == "announcements" and len(key) >= 3 and isinstance(key[2], str):
            return key[2]
    return None


def items_from_hydration(hydration: dict[str, Any]) -> list[dict[str, Any]]:
    """Every announcement record hydrated into the page."""
    items: list[dict[str, Any]] = []
    for q in _queries(hydration):
        if (q.get("queryKey") or [None])[0] != "announcements":
            continue
        data = (q.get("state", {}).get("data", {}) or {}).get("data") or []
        items.extend(data)
    return items


def announcement_year(item: dict[str, Any]) -> int:
    """The fiscal year an announcement is about: the title's own year where it
    has one, else the year it was released."""
    title = item.get("title", "") or ""
    m = re.search(r"20(\d\d)", title) or re.search(r"FY(\d\d)\b", title)
    if m:
        return 2000 + int(m[1])
    return dt.datetime.fromtimestamp(item.get("releaseDate", 0) or 0).year


def plan_from_items(items: list[dict[str, Any]], held: set[str], after_year: int,
                    ticker: str) -> dict[tuple[str, str], str]:
    """Map (kind, period) -> announcement id for everything worth fetching.

    Items arrive newest-first, so the first announcement seen for a period
    wins. `held` holds filename stems already on disk (PDF or extracted text);
    `after_year` is an exclusive floor (0 = seed, take everything).
    """
    plan: dict[tuple[str, str], str] = {}
    for a in items:
        kind = TYPES.get(str(a.get("type") or ""))
        if not kind:
            continue
        year = announcement_year(a)
        if after_year and year <= after_year:
            continue
        period = f"FY{year}" if kind == "Annual" else f"H1-{year}"
        if f"{ticker}_{kind}_{period}" in held:
            continue
        plan.setdefault((kind, period), a["id"])
    return plan


def held_stems(ticker: str, repo: Path = REPO) -> set[str]:
    """Filename stems already on file for `ticker`, PDF or extracted text."""
    base = repo / "research" / ticker
    return {p.stem
            for sub, ext in (("PDFs", "pdf"), ("Extracted", "txt"))
            for p in (base / sub).glob(f"{ticker}_*.{ext}")}


def filename(ticker: str, kind: str, period: str) -> str:
    return f"{ticker}_{kind}_{period}.pdf"


# --- planning with I/O -----------------------------------------------------

def plan(ticker: str, after_year: int, repo: Path = REPO,
         deadline: Deadline | None = None) -> dict[tuple[str, str], str]:
    """The full plan, including the back-year listings a seed run needs."""
    code = ticker.split(".")[0]
    hydration = next_data(f"{SITE}/companies/{code}/announcements")
    items = items_from_hydration(hydration)
    if not items:
        raise RuntimeError(f"nzx: no hydrated announcements for {ticker}")

    if after_year == 0:
        company_id = company_id_from_hydration(hydration)
        if company_id:
            this_year = dt.date.today().year
            for year in range(this_year - 1, this_year - 1 - BACK_YEARS, -1):
                if deadline is not None:
                    deadline.check(f"the {year} listing")
                url = f"{API}/company/{company_id}/announcements/{year}/all.json"
                try:
                    raw = json.loads(get(url))
                except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
                    print(f"nzx: {year} listing failed ({e})")
                    continue
                items.extend((raw.get("data") if isinstance(raw, dict) else raw) or [])
        else:
            print("nzx: no companyId in hydration -- current year only")

    return plan_from_items(items, held_stems(ticker, repo), after_year, ticker)


def attachment_urls(ann_id: str) -> list[str]:
    """Every PDF attached to an announcement, deduplicated and ordered."""
    detail = next_data(f"{SITE}/announcements/{ann_id}")
    blob = json.dumps(detail)
    found = set(PDF_RE.findall(blob))
    # A stubbed detail payload may hand back the list directly.
    for v in (detail.get("pdfs") or []) if isinstance(detail, dict) else []:
        found.add(str(v))
    return sorted(found)


def fetch(ticker: str, dest: Path, after_year: int, repo: Path = REPO,
          deadline_s: float = DEFAULT_DEADLINE) -> int:
    """Download every planned announcement's fattest PDF into `dest`.

    Returns the number of files written. Raises TimeoutError once the
    deadline is spent, and RuntimeError if the listing itself is unusable.
    """
    deadline = Deadline(deadline_s)
    deadline.check("the announcements listing")
    todo = plan(ticker, after_year, repo, deadline)
    if not todo:
        print(f"nzx: nothing newer than {after_year} for {ticker}")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    ok = 0
    for (kind, period), ann_id in todo.items():
        deadline.check(filename(ticker, kind, period))
        try:
            pdfs = attachment_urls(ann_id)
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
            print(f"nzx: announcement {ann_id} unreachable ({e}); skipping")
            continue
        if not pdfs:
            print(f"nzx: no PDF attachments on announcement {ann_id}")
            continue
        best = max(pdfs, key=head_size)
        out = dest / filename(ticker, kind, period)
        try:
            download(best, out, deadline)
        except (subprocess.SubprocessError, OSError) as e:
            print(f"nzx: FAILED {out.name}: {e}")
            out.unlink(missing_ok=True)
            continue
        if not is_pdf(out):
            out.unlink(missing_ok=True)
            print(f"nzx: skipped non-PDF payload for {out.name}")
            continue
        print(f"nzx: downloaded {out.name}")
        ok += 1
    print(f"nzx: {ok}/{len(todo)} downloaded")
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
        print(f"nzx: {e}")
        return 3
    except (RuntimeError, subprocess.SubprocessError, OSError) as e:
        print(f"nzx: adapter failed ({e})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
