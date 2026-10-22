#!/usr/bin/env python3
"""Deterministic filing-text adapter for US filers, backed by SEC EDGAR.

US filers take the XBRL route for their statements (build_facts_xbrl.py),
but XBRL carries no operating KPIs: DAUq, ARPU, subscribers, nights booked
and the like are printed only in the 10-K/10-Q text. Before this adapter no
US ticker had filing text on file, so the financial-parser had nothing to
read KPIs from -- 302 of 303 XBRL-only runs since 2026-09-15 stored none.

The plan is a pure function of EDGAR's submissions JSON:

    https://data.sec.gov/submissions/CIK##########.json

whose `filings.recent` arrays list every filing with its form, report date
and primary document. Only original 10-K and 10-Q filings are planned
(amendments restate pieces, 8-Ks are mostly not reports). EDGAR is keyed by
CIK, so the wrong-company risk filing_gate exists for does not arise here;
HTML is extracted straight to Extracted/*.txt with extract_sec_html, which
keeps table rows on one line.

    python3 scripts/adapters/sec.py RDDT [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters import DEFAULT_DEADLINE, DOWNLOAD_TIMEOUT, LISTING_TIMEOUT, Deadline

REPO = Path(__file__).resolve().parents[2]
# SEC fair-access policy: a declared User-Agent with a contact, <=10 req/s.
UA = "swilliams@intellum.com stock-research"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
FORMS = {"10-K": "Annual", "10-Q": "Quarterly"}
SEED_ANNUALS = 3
SEED_QUARTERLIES = 8
# Below this many characters an extracted "filing" is an error page.
MIN_TEXT_CHARS = 2000
PAUSE_S = 0.15


@dataclass(frozen=True)
class Planned:
    name: str        # e.g. RDDT_Quarterly_Q2-2026.txt
    url: str
    form: str
    report_date: str


# --- network I/O (monkeypatched in tests) ----------------------------------

def get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=LISTING_TIMEOUT) as r:
        return json.loads(r.read())


def download(url: str, out: Path, deadline: Deadline | None = None) -> None:
    budget = DOWNLOAD_TIMEOUT
    if deadline is not None:
        budget = max(1, min(budget, int(deadline.remaining)))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=budget) as r:
        out.write_bytes(r.read())
    time.sleep(PAUSE_S)


# --- pure functions --------------------------------------------------------

def fye_month(submissions: dict[str, Any]) -> int:
    """The fiscal year-end month EDGAR declares ("1231" -> 12)."""
    raw = str(submissions.get("fiscalYearEnd") or "1231")
    return int(raw[:2]) if raw[:2].isdigit() and 1 <= int(raw[:2]) <= 12 else 12


def fiscal_label(report_date: str, fye: int, form: str) -> str:
    """FY#### for a 10-K, Q#-#### (fiscal) for a 10-Q.

    A 52/53-week period can close a few days into the next calendar month
    (2026-01-03 closes FY2025); those are read as the month before.
    """
    d = dt.date.fromisoformat(report_date)
    year, month = d.year, d.month
    if d.day <= 7:
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    fiscal_year = year if month <= fye else year + 1
    if form == "10-K":
        return f"FY{fiscal_year}"
    offset = (month - fye) % 12
    quarter = max(1, min(3, round(offset / 3)))
    return f"Q{quarter}-{fiscal_year}"


def plan(submissions: dict[str, Any], ticker: str, held: set[str],
         annuals: int = SEED_ANNUALS,
         quarterlies: int = SEED_QUARTERLIES) -> list[Planned]:
    """The newest `annuals` 10-Ks and `quarterlies` 10-Qs, newest first,
    minus the stems already held."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    cik = int(str(submissions.get("cik") or "0"))
    fye = fye_month(submissions)
    rows = sorted(
        (r for r in (dict(zip(recent, vals, strict=True))
                   for vals in zip(*recent.values(), strict=True))
         if r.get("form") in FORMS and r.get("reportDate")
         and r.get("primaryDocument")),
        key=lambda r: r["reportDate"], reverse=True)

    budget = {"10-K": annuals, "10-Q": quarterlies}
    seen: set[str] = set()
    out: list[Planned] = []
    for r in rows:
        form = r["form"]
        if budget[form] <= 0:
            continue
        stem = f"{ticker}_{FORMS[form]}_{fiscal_label(r['reportDate'], fye, form)}"
        if stem in seen:
            continue
        seen.add(stem)
        budget[form] -= 1
        if stem in held:
            continue
        url = ARCHIVE.format(cik=cik, acc=r["accessionNumber"].replace("-", ""),
                             doc=r["primaryDocument"])
        out.append(Planned(f"{stem}.txt", url, form, r["reportDate"]))
    return out


def held_stems(ticker: str, repo: Path = REPO) -> set[str]:
    extracted = repo / "research" / ticker / "Extracted"
    return {p.stem for p in extracted.glob(f"{ticker}_*.txt")} if extracted.is_dir() else set()


# --- execution -------------------------------------------------------------

def fetch(submissions: dict[str, Any], ticker: str, dest: Path, held: set[str],
          annuals: int = SEED_ANNUALS, quarterlies: int = SEED_QUARTERLIES,
          deadline_s: float = DEFAULT_DEADLINE) -> list[Path]:
    """Download and extract every planned filing into `dest`. Returns the
    text files written; raises TimeoutError when the budget runs out."""
    from extract_sec_html import extract

    deadline = Deadline(deadline_s)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for p in plan(submissions, ticker, held, annuals, quarterlies):
        deadline.check(p.name)
        raw = dest / (Path(p.name).stem + ".htm")
        text = dest / p.name
        try:
            download(p.url, raw, deadline)
            extract(str(raw), str(text))
        finally:
            raw.unlink(missing_ok=True)
        body = text.read_text(errors="replace") if text.exists() else ""
        if len(body.strip()) < MIN_TEXT_CHARS:
            text.unlink(missing_ok=True)
            print(f"  [skipped] {p.name} -- extracted text too short ({len(body)} chars)")
            continue
        print(f"  [extracted] {p.name} <- {p.url}")
        written.append(text)
    return written


def submissions_for(ticker: str) -> dict[str, Any] | None:
    import build_facts_xbrl

    cik = build_facts_xbrl.cik_for(ticker)
    return get_json(SUBMISSIONS.format(cik=cik)) if cik else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ticker")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    a = ap.parse_args(argv)
    ticker = a.ticker.upper()
    subs = submissions_for(ticker)
    if subs is None:
        print(f"{ticker}: not in SEC's ticker map")
        return 1
    held = held_stems(ticker)
    if a.dry_run:
        for p in plan(subs, ticker, held):
            print(f"  {p.name:40s} <- {p.url}")
        return 0
    fetch(subs, ticker, REPO / "research" / ticker / "Extracted", held,
          deadline_s=a.deadline)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.exit(main())
