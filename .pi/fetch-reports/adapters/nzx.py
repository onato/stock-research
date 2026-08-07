#!/usr/bin/env python3
"""Deterministic NZX adapter — update mode only.

nzx.com is Next.js; the company announcements page hydrates the CURRENT
year's announcements (typed ANNREP/HALFYR/FLLYR...) in __NEXT_DATA__, and
attachment PDFs download from a public api.nzx.com URL. Back-years are not
in the initial HTML (the listing API is 403 to scripts), so this adapter
only serves the nightly update case: anything newer than what we hold.
Seeding still falls through to the archive/model path.

Fattest-attachment rule (ASX cover-letter lesson): when an announcement has
several PDFs, take the largest by Content-Length.

Exit 0 = ran cleanly; nonzero = failed (caller falls back).

Usage: nzx.py TAH.NZ --dest DIR --after-year 2025
"""
import json
import re
import subprocess
import sys
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
TYPES = {"ANNREP": "Annual", "HALFYR": "HalfYear"}
ALSO_IF_REPORT = {"FLLYR": "Annual", "INTERIM": "HalfYear"}


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
    if not after:
        print("nzx-adapter: update-mode only (needs --after-year > 0)")
        return 1
    code = ticker.split(".")[0]

    d = next_data(f"https://www.nzx.com/companies/{code}/announcements")
    items = []
    for q in d.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", []):
        if q.get("queryKey", [None])[0] == "announcements":
            items += (q.get("state", {}).get("data", {}) or {}).get("data", []) or []
    if not items:
        print(f"nzx-adapter: no hydrated announcements for {ticker}")
        return 1

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
            import datetime
            year = datetime.datetime.fromtimestamp(a.get("releaseDate", 0)).year
        if year <= after:
            continue
        period = f"FY{year}" if typ == "Annual" else f"H1-{year}"
        plan.setdefault((typ, period), a["id"])

    if not plan:
        print(f"nzx-adapter: nothing newer than {after} for {ticker}")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    ok = 0
    for (typ, period), ann_id in plan.items():
        detail = next_data(f"https://www.nzx.com/announcements/{ann_id}")
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
            print(f"nzx-adapter: downloaded {out.name}")
            ok += 1
        except Exception as e:
            print(f"nzx-adapter: FAILED {out.name}: {e}")
            out.unlink(missing_ok=True)
    print(f"nzx-adapter: {ok}/{len(plan)} downloaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
