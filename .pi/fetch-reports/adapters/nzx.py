#!/usr/bin/env python3
"""Deterministic NZX adapter — updates and full seeding.

nzx.com is Next.js; the company announcements page hydrates the CURRENT
year's announcements (typed ANNREP/HALFYR/FLLYR...) in __NEXT_DATA__, and
attachment PDFs download from a public api.nzx.com URL. Back-years come
from the per-year listing the site's own client uses,
  api.nzx.com/public/company/{CompanyID}/announcements/{YEAR}/all.json
(the CompanyID, e.g. FPH000000, is read from the page hydration — it is
NOT always the ticker code, e.g. AFC.NZ is IRG000000). Seed mode
(--after-year 0) walks the last BACK_YEARS of listings; periods already
held in research/{TICKER}/ are skipped, so seeding and thin-holdings
backfill are the same call.

Fattest-attachment rule (ASX cover-letter lesson): when an announcement has
several PDFs, take the largest by Content-Length.

Exit 0 = ran cleanly; nonzero = failed (caller falls back).

Usage: nzx.py TAH.NZ --dest DIR --after-year 2025
"""
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

BACK_YEARS = 8

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
# Small NZX companies often never lodge a glossy ANNREP — the FLLYR results
# announcement (with financial statements attached) IS the annual filing.
TYPES = {"ANNREP": "Annual", "HALFYR": "HalfYear", "FLLYR": "Annual", "INTERIM": "HalfYear"}
ALSO_IF_REPORT = {}


def get(url: str) -> str:
    return subprocess.run(["curl", "-sfL", "--max-time", "30", url, "-A", UA],
                          capture_output=True, text=True, timeout=40, check=True).stdout


def next_data(url: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  get(url), re.S)
    return json.loads(m[1]) if m else {}


def head_size(url: str) -> int:
    out = subprocess.run(["curl", "-sIL", "--max-time", "20", url, "-A", UA],
                         capture_output=True, text=True, timeout=30).stdout
    m = re.findall(r"content-length:\s*(\d+)", out, re.I)
    return int(m[-1]) if m else 0


def main() -> int:
    ticker = sys.argv[1]
    args = sys.argv[2:]
    dest = Path(args[args.index("--dest") + 1])
    after = int(args[args.index("--after-year") + 1]) if "--after-year" in args else 0
    # after=0 (seed): bootstrap from whatever current-year announcements are
    # hydrated in the page — usually the latest annual/half-year, enough to
    # give a placeholder ticker its first real filings and enable updates.
    code = ticker.split(".")[0]

    try:
        d = next_data(f"https://www.nzx.com/companies/{code}/announcements")
    except subprocess.CalledProcessError as e:
        # Delisted/renamed codes fail the page fetch; that is a clean
        # fall-back-to-model case, not a crash.
        print(f"nzx-adapter: announcements page unreachable for {ticker} ({e})")
        return 1
    items = []
    company_id = None
    for q in d.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", []):
        key = q.get("queryKey") or [None]
        if key[0] == "announcements":
            items += (q.get("state", {}).get("data", {}) or {}).get("data", []) or []
            if len(key) >= 3:
                company_id = key[2]
    if not items:
        print(f"nzx-adapter: no hydrated announcements for {ticker}")
        return 1

    if after == 0:
        if company_id:
            this_year = datetime.date.today().year
            for year in range(this_year - 1, this_year - 1 - BACK_YEARS, -1):
                url = (f"https://api.nzx.com/public/company/{company_id}"
                       f"/announcements/{year}/all.json")
                try:
                    raw = json.loads(get(url))
                    items += (raw.get("data") if isinstance(raw, dict) else raw) or []
                except Exception as e:
                    print(f"nzx-adapter: {year} listing failed ({e})")
        else:
            print("nzx-adapter: no companyId in hydration — current year only")

    plan = {}
    for a in items:
        typ = TYPES.get(a.get("type"))
        if not typ and a.get("type") in ALSO_IF_REPORT and "report" in a.get("title", "").lower():
            typ = ALSO_IF_REPORT[a["type"]]
        if not typ:
            continue
        title = a.get("title", "")
        m = re.search(r"20(\d\d)", title) or re.search(r"FY(\d\d)\b", title)
        year = 2000 + int(m[1]) if m else None
        if year is None:
            year = datetime.datetime.fromtimestamp(a.get("releaseDate", 0)).year
        if after and year <= after:
            continue
        period = f"FY{year}" if typ == "Annual" else f"H1-{year}"
        plan.setdefault((typ, period), a["id"])

    # Skip periods already held (PDF or extracted text) so seed mode doubles
    # as backfill for thin holdings and re-runs stay idempotent.
    repo = Path(__file__).resolve().parents[3]
    held = {p.stem for sub, ext in (("PDFs", "pdf"), ("Extracted", "txt"))
            for p in (repo / "research" / ticker / sub).glob(f"{ticker}_*.{ext}")}
    plan = {k: v for k, v in plan.items()
            if f"{ticker}_{k[0]}_{k[1]}" not in held}

    if not plan:
        print(f"nzx-adapter: nothing newer than {after} for {ticker}")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    ok = 0
    for (typ, period), ann_id in plan.items():
        try:
            detail = next_data(f"https://www.nzx.com/announcements/{ann_id}")
        except subprocess.CalledProcessError as e:
            print(f"nzx-adapter: announcement {ann_id} unreachable ({e}); skipping")
            continue
        pdfs = sorted(set(re.findall(
            r"https?://api\.nzx\.com/public/announcement/[^\"\\s]+?\.pdf",
            json.dumps(detail))))
        if not pdfs:
            print(f"nzx-adapter: no PDF attachments on announcement {ann_id}")
            continue
        best = max(pdfs, key=head_size)
        out = dest / f"{ticker}_{typ}_{period}.pdf"
        try:
            subprocess.run(["curl", "-sfL", "--max-time", "300", best, "-A", UA,
                            "-o", str(out)], timeout=320, check=True)
            with open(out, "rb") as fh:  # MAGIC-CHECKED: manifests/HTML are not reports
                if fh.read(5) != b"%PDF-":
                    out.unlink(missing_ok=True)
                    print(f"skipped non-PDF payload for {out.name}")
                    continue
            print(f"nzx-adapter: downloaded {out.name}")
            ok += 1
        except Exception as e:
            print(f"nzx-adapter: FAILED {out.name}: {e}")
            out.unlink(missing_ok=True)
    print(f"nzx-adapter: {ok}/{len(plan)} downloaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
