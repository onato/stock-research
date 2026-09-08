#!/usr/bin/env python3
"""Deterministic gate between a staged download and research/{TICKER}/PDFs.

Nothing an adapter -- or the ir-scraper agent -- downloads reaches research/
on its own say-so. A staged file is promoted only if:

  1. its name matches {TICKER}_(Annual|HalfYear|Quarterly|Presentation)_{Period}.pdf
  2. it really is a PDF (magic bytes -- IR sites serve 403 HTML with a .pdf URL)
  3. it is at least 30 KB (a cover letter is not a report)
  4. pdftotext gets non-trivial text out of it (scanned/font-corrupt PDFs)
  5. the extracted text mentions the company (wrong-company downloads)
  6. it does not overwrite a file already on disk

`normalize_name` first repairs the mechanical label malformations that show up
in hand-written filenames (FY26 -> FY2026, 1H26/HY26/H1-FY26 -> H1-2026), so a
merely-abbreviated label is fixed rather than rejected.

Both entry points are pure functions of their arguments: `check` takes the
company name and destination directory rather than looking them up, so the
whole policy is testable without a repo.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

KINDS = ("Annual", "HalfYear", "Quarterly", "Presentation")
NAME_RE = re.compile(
    rf"^(?P<ticker>.+?)_(?P<kind>{'|'.join(KINDS)})_(?P<period>[A-Za-z0-9-]+)\.pdf$")
MIN_BYTES = 30_000
MIN_TEXT_CHARS = 200
TEXT_PAGES = 20

# Words too common to identify a company: "Holdings Limited" appears on half
# the NZX. The name check needs a distinctive word or the ticker base.
STOPWORDS = frozenset({
    "the", "plc", "limited", "ltd", "group", "holdings", "company",
    "corporation", "inc", "incorporated", "and", "of",
})


def normalize_name(name: str) -> str:
    """Repair mechanical label malformations in a filename.

    FY26 -> FY2026; H1-FY26 / H1-26 / 1H26 / HY26 -> H1-2026; Q3-25 -> Q3-2025.
    Idempotent: a canonical name is returned unchanged.
    """
    fixed = re.sub(r"_FY(\d{2})\.pdf$", r"_FY20\1.pdf", name)
    # (?:FY)? not FY? -- "H1-26" carries no F at all, only "H1-FY26" does.
    fixed = re.sub(r"_(H[12]|Q[1-4])-(?:FY)?(\d{2})\.pdf$", r"_\1-20\2.pdf", fixed)
    fixed = re.sub(r"_([12])H(\d{2})\.pdf$", r"_H\1-20\2.pdf", fixed)
    return re.sub(r"_HY(\d{2})\.pdf$", r"_H1-20\1.pdf", fixed)


def pdf_text(path: Path) -> str:
    """First TEXT_PAGES pages of `path` as text. Separated from `check` so a
    test can stub the extraction without a stub PDF for every failure mode."""
    return subprocess.run(
        ["pdftotext", "-l", str(TEXT_PAGES), str(path), "-"],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout


def name_words(company: str) -> list[str]:
    """The distinctive words of a company name, longest-lived first."""
    words = [w.strip("&.,()").lower() for w in company.split()]
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def check(path: Path, ticker: str, company: str, dest: Path) -> str | None:
    """Return a rejection reason, or None if `path` may be promoted to `dest`."""
    m = NAME_RE.match(path.name)
    if not m or m["ticker"] != ticker:
        return "bad-filename"
    with open(path, "rb") as fh:
        if fh.read(5) != b"%PDF-":
            return "not-a-pdf"
    size = path.stat().st_size
    if size < MIN_BYTES:
        return f"too-small ({size} bytes)"
    try:
        text = pdf_text(path)
    except Exception as e:
        return f"pdftotext-failed ({e})"
    if len(text.strip()) < MIN_TEXT_CHARS:
        return "no-extractable-text"
    # An unknown company (companies.json had no entry, so the caller passed the
    # ticker or an empty string) skips the check rather than failing everything.
    if company and company != ticker:
        # Two words is enough to identify: covers say "Skellerup", not
        # "Skellerup Holdings", and a registered name longer than the cover
        # name still matches on its leading words.
        words = name_words(company)[:2]
        base = ticker.split(".")[0].lower()
        lowered = text.lower()
        # The ticker base rescues filings that print the code rather than the
        # name, but only as a whole word: "SYN" is a substring of "synthetic"
        # and would otherwise pass a Fisher & Paykel report as SYN.NZ's.
        base_hit = re.search(rf"\b{re.escape(base)}\b", lowered) is not None
        if words and not any(w in lowered for w in words) and not base_hit:
            return f"company-name-missing (neither {words} nor {base!r} in first {TEXT_PAGES} pages)"
    if (dest / path.name).exists():
        return "already-exists"
    return None


def company_name(ticker: str, repo: Path = REPO) -> str:
    """The company's name: research/{T}/info.json wins, state/companies.json is
    the fallback, and an unknown ticker returns the ticker (which `check`
    reads as "skip the name check")."""
    info = repo / "research" / ticker / "info.json"
    if info.exists():
        try:
            name = json.loads(info.read_text()).get("name")
        except json.JSONDecodeError:
            name = None
        if name:
            return str(name)
    companies = repo / "state" / "companies.json"
    if companies.exists():
        try:
            entry = json.loads(companies.read_text()).get(ticker) or {}
        except json.JSONDecodeError:
            entry = {}
        if entry.get("name"):
            return str(entry["name"])
    return ticker


def log_verdicts(log: Path, results: Iterable[dict[str, str]]) -> None:
    """Append one JSON line per verdict to `log`, timestamped as one batch."""
    log.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    with open(log, "a") as fh:
        fh.writelines(json.dumps({"ts": stamp, **r}) + "\n" for r in results)
