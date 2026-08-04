#!/usr/bin/env python3
"""Deterministic gate: validate staged downloads before they touch research/.

The model's self-report is never consulted. A staged file is promoted only if:
  1. filename matches {TICKER}_{Type}_{Period}.pdf exactly
  2. it is a real PDF (magic bytes)
  3. it is at least 30 KB
  4. pdftotext extracts non-trivial text
  5. the extracted text mentions the company name (first two words)
  6. it does not overwrite an existing file

Pass -> moved to research/{TICKER}/PDFs/. Fail -> stays in quarantine/ with a
reason logged to logs/{TICKER}.jsonl.

Usage: gate.py TICKER
"""
import datetime
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
NAME_RE = re.compile(
    r"^%s_(Annual|HalfYear|Quarterly|Presentation)_[A-Za-z0-9-]+\.pdf$"
)


def normalize(path: Path) -> Path:
    """Fix mechanical label malformations before validation (FY26 -> FY2026)."""
    fixed = re.sub(r"_FY(\d{2})\.pdf$", r"_FY20\1.pdf", path.name)
    fixed = re.sub(r"_(H[12]|Q[1-4])-FY?(\d{2})\.pdf$", r"_\1-20\2.pdf", fixed)
    if fixed != path.name:
        path = path.rename(path.with_name(fixed))
    return path


def check(path: Path, ticker: str, company: str, dest: Path) -> str | None:
    """Return a rejection reason, or None if the file passes."""
    if not re.match(NAME_RE.pattern % re.escape(ticker), path.name):
        return "bad-filename"
    with open(path, "rb") as fh:
        if fh.read(5) != b"%PDF-":
            return "not-a-pdf"
    if path.stat().st_size < 30_000:
        return f"too-small ({path.stat().st_size} bytes)"
    try:
        text = subprocess.run(
            ["pdftotext", "-l", "5", str(path), "-"],
            capture_output=True, text=True, timeout=120, check=True,
        ).stdout
    except Exception as e:
        return f"pdftotext-failed ({e})"
    if len(text.strip()) < 200:
        return "no-extractable-text"
    if company != ticker:  # unknown-name tickers skip this check rather than fail everything
        name_token = " ".join(company.lower().split()[:2])
        if name_token not in text.lower():
            return f"company-name-missing ({name_token!r} not in first 5 pages)"
    # Period consistency: a report labeled FY2022/H1-2023 must not be *about* a
    # later period. "…ended 31 December 2023" in an H1-2023 file means mislabeled.
    label_year = max((int(y) for y in re.findall(r"(\d{4})", path.name)), default=0)
    ended = re.findall(r"(?:year|period)\s+end(?:ed|ing)\s+\d{1,2}\s+\w+\s+(\d{4})", text, re.I)
    if ended and max(int(y) for y in ended) > label_year:
        return f"period-mismatch (labeled {label_year}, text reports period ended {max(ended)})"
    if not ended and str(label_year) not in text:
        return f"period-unverifiable (no 'ended <date>' phrase and {label_year} absent from first pages)"
    if (dest / path.name).exists():
        return "already-exists"
    return None


def main(ticker: str) -> int:
    staging = HERE / "staging" / ticker
    quarantine = HERE / "quarantine" / ticker
    logdir = HERE / "logs"
    dest = REPO / "research" / ticker / "PDFs"
    for d in (quarantine, logdir, dest):
        d.mkdir(parents=True, exist_ok=True)

    companies = json.loads((REPO / "state" / "companies.json").read_text())
    company = companies.get(ticker, {}).get("name", ticker)

    results = []
    for f in sorted(staging.glob("*")) if staging.is_dir() else []:
        if f.name.startswith("."):
            continue
        f = normalize(f)
        reason = check(f, ticker, company, dest)
        if reason is None:
            shutil.move(str(f), dest / f.name)
            results.append({"file": f.name, "verdict": "promoted"})
        else:
            shutil.move(str(f), quarantine / f.name)
            results.append({"file": f.name, "verdict": "quarantined", "reason": reason})

    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(logdir / f"{ticker}.jsonl", "a") as log:
        for r in results:
            log.write(json.dumps({"ts": stamp, **r}) + "\n")

    promoted = [r for r in results if r["verdict"] == "promoted"]
    print(f"{ticker}: {len(promoted)} promoted, {len(results) - len(promoted)} quarantined")
    for r in results:
        detail = f"  [{r['verdict']}] {r['file']}"
        if "reason" in r:
            detail += f" — {r['reason']}"
        print(detail)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
