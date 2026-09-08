#!/usr/bin/env python3
"""Download a ticker's filings deterministically, no agent involved.

    python3 scripts/fetch_filings.py SMI.NZ [--years 2019-2026] [--dry-run]

Why this exists: the ir-scraper agent (sonnet, ~$0.88 a run, 33 turns)
brute-forces IR websites -- on SMI.NZ it spent three and a half minutes
reverse-engineering an announcements widget that has a public JSON API. Every
exchange with a deterministic route gets one here, and the agent is left to
do only what genuinely needs judgement: presentations, unusual titles, and
exchanges with no adapter.

Dispatch is by suffix:

    .AX -> fetch_asx      (ASX announcement listings; downloads straight to
                           research/, already gated by its own checks)
    .NZ -> adapters.nzx   (nzx.com hydration + the per-year listing API)
    .HK -> adapters.hkex  (HKEXnews title search)

plus, for a .L/.AX/.NZ ticker holding fewer than SEED_THRESHOLD filings, the
AnnualReports.com archive as a seed.

Adapters download into state/staging/{T}/. Nothing reaches research/ on an
adapter's say-so: every staged file goes past `filing_gate.check` first, and
survivors are moved to research/{T}/PDFs and extracted to Extracted/.
Rejects go to state/staging/quarantine/{T}/ with a reason logged.

Exit codes are the contract with the orchestrator:

    0  ran cleanly (even if nothing was new)
    1  the adapter itself failed -- fall back to the ir-scraper agent
    3  the deadline was spent (a stalled source must not cost the night;
       HKEX's titleSearchServlet once hung and one ticker took five hours)
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

import fetch_asx
import filing_gate
from adapters import DEFAULT_DEADLINE, annualreports, hkex, nzx

REPO = Path(__file__).resolve().parents[1]

# Below this many filings a ticker counts as unseeded and gets the archive
# backfill as well as its exchange adapter.
SEED_THRESHOLD = 3
# Only these suffixes exist in the AnnualReports.com archive.
SEED_SUFFIXES = (".L", ".AX", ".NZ")


def staging_dir(ticker: str) -> Path:
    return REPO / "state" / "staging" / ticker


def quarantine_dir(ticker: str) -> Path:
    return REPO / "state" / "staging" / "quarantine" / ticker


def log_path(ticker: str) -> Path:
    return REPO / "state" / "staging" / "logs" / f"{ticker}.jsonl"


def holdings_count(ticker: str) -> int:
    """Distinct filings on file, counting a PDF and its extracted text once."""
    base = REPO / "research" / ticker
    return len({p.stem
                for sub, ext in (("PDFs", "pdf"), ("Extracted", "txt"))
                for p in (base / sub).glob(f"{ticker}_*.{ext}")})


def newest_year(ticker: str) -> int:
    """The latest fiscal year already held -- the adapters' exclusive floor.

    0 (nothing on file) puts an adapter in seed mode.
    """
    years = [int(y)
             for stem in nzx.held_stems(ticker, REPO)
             for y in re.findall(r"(20\d\d)", stem)]
    return max(years, default=0)


def extract(pdf: Path, out: Path) -> None:
    """pdftotext -layout, the pipeline's step 2. A promoted filing arrives
    already extracted so the ticker is parser-ready without a second pass."""
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["pdftotext", "-layout", str(pdf), str(out)],
                       capture_output=True, timeout=300, check=True)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"  extract-failed {pdf.name}: {e}")


def promote(ticker: str) -> tuple[int, int, list[str]]:
    """Gate every staged file. Returns (promoted, quarantined, reasons)."""
    stage = staging_dir(ticker)
    dest = REPO / "research" / ticker / "PDFs"
    extracted = REPO / "research" / ticker / "Extracted"
    company = filing_gate.company_name(ticker, REPO)

    results: list[dict[str, str]] = []
    for downloaded in sorted(stage.glob("*.pdf")) if stage.is_dir() else []:
        fixed = filing_gate.normalize_name(downloaded.name)
        staged = (downloaded.rename(downloaded.with_name(fixed))
                  if fixed != downloaded.name else downloaded)
        reason = filing_gate.check(staged, ticker, company, dest)
        if reason is None:
            dest.mkdir(parents=True, exist_ok=True)
            final = dest / staged.name
            shutil.move(str(staged), final)
            text = extracted / (final.stem + ".txt")
            if not text.exists():
                extract(final, text)
            results.append({"file": final.name, "verdict": "promoted"})
        else:
            q = quarantine_dir(ticker)
            q.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), q / staged.name)
            results.append({"file": staged.name, "verdict": "quarantined",
                            "reason": reason})

    if results:
        filing_gate.log_verdicts(log_path(ticker), results)
    promoted = [r for r in results if r["verdict"] == "promoted"]
    rejected = [r for r in results if r["verdict"] == "quarantined"]
    for r in results:
        line = f"  [{r['verdict']}] {r['file']}"
        if "reason" in r:
            line += f" -- {r['reason']}"
        print(line)
    return len(promoted), len(rejected), [r["reason"] for r in rejected]


def run_adapters(ticker: str, years: str | None, deadline_s: float,
                 dry_run: bool) -> None:
    """Fetch (or, on a dry run, print) everything the adapters plan.

    Raises TimeoutError on a spent deadline and any other exception for an
    adapter failure; the caller maps those to exit codes.
    """
    suffix = "." + ticker.rsplit(".", 1)[-1] if "." in ticker else ""
    dest = staging_dir(ticker)
    after = newest_year(ticker)
    thin = holdings_count(ticker) < SEED_THRESHOLD

    if suffix == ".NZ":
        if dry_run:
            for kind, period in nzx.plan(ticker, after, repo=REPO):
                print(f"  {nzx.filename(ticker, kind, period)}")
        else:
            nzx.fetch(ticker, dest, after, repo=REPO, deadline_s=deadline_s)
    elif suffix == ".HK":
        if dry_run:
            for name, url, _year in hkex.plan(ticker, after):
                print(f"  {name:40s} <- {url}")
        else:
            hkex.fetch(ticker, dest, after, deadline_s=deadline_s)
    elif suffix == ".AX":
        # fetch_asx is already deterministic and applies its own checks, so
        # it writes straight to research/{T}/PDFs rather than through staging.
        argv = [ticker, "--years", years or f"2016-{dt.date.today().year}"]
        if dry_run:
            argv.append("--dry-run")
        fetch_asx.main(argv)
    else:
        return

    if thin and suffix in SEED_SUFFIXES:
        if dry_run:
            print("  (thin holdings: would also seed from the "
                  "AnnualReports.com archive)")
        else:
            annualreports.fetch(ticker, dest, after, repo=REPO,
                                deadline_s=deadline_s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ticker", help="e.g. SMI.NZ")
    ap.add_argument("--years", help="ASX only: 2019-2026 (default 2016-this year)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, fetch nothing")
    ap.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE,
                    help="wall-clock budget in seconds (default 600)")
    a = ap.parse_args(argv)
    ticker = a.ticker.upper()
    suffix = "." + ticker.rsplit(".", 1)[-1] if "." in ticker else ""

    if suffix not in (".NZ", ".HK", ".AX"):
        print(f"{ticker}: no deterministic adapter for {suffix or 'this ticker'} "
              f"-- use the ir-scraper agent (US filers use the SEC XBRL route)")
        return 1

    print(f"{ticker}: {holdings_count(ticker)} filings on file, "
          f"fetching anything after FY{newest_year(ticker)}")
    try:
        run_adapters(ticker, a.years, a.deadline, a.dry_run)
    except TimeoutError as e:
        print(f"{ticker}: {e}")
        promote(ticker)      # keep whatever landed before the clock ran out
        return 3
    except Exception as e:
        print(f"{ticker}: adapter failed -- {e}")
        return 1

    if a.dry_run:
        return 0

    stage = staging_dir(ticker)
    downloaded = len(list(stage.glob("*.pdf"))) if stage.is_dir() else 0
    promoted, quarantined, reasons = promote(ticker)
    print(f"{ticker}: downloaded {downloaded}, promoted {promoted}, "
          f"quarantined {quarantined}")
    for reason in reasons:
        print(f"  quarantine reason: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
