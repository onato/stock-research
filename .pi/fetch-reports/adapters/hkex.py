#!/usr/bin/env python3
"""Deterministic HKEX filing adapter — no model involved.

Resolves the ticker on HKEXnews, lists annual (t2code 40100) and interim
(40200) reports, and downloads anything newer than --after-year into --dest
using the repo naming convention. gate.py still validates everything after.

Exit codes: 0 = ran cleanly (even if nothing to download), nonzero = adapter
failed and the caller should fall back to the model.

Usage: hkex.py 1211.HK --dest DIR [--after-year 2023]
"""
import json
import re
import subprocess
import sys
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
BASE = "https://www1.hkexnews.hk"
SEED_ANNUALS = 8   # when starting from nothing, how many years back to fetch
SEED_INTERIMS = 2
SEED_QUARTERLIES = 4


def get(url: str) -> str:
    return subprocess.run(
        ["curl", "-sf", "--max-time", "30", url, "-H", f"User-Agent: {UA}"],
        capture_output=True, text=True, timeout=40, check=True).stdout


def stock_id(code: str) -> int:
    raw = get(f"{BASE}/search/prefix.do?callback=cb&lang=EN&type=A&name={code}&market=SEHK")
    info = json.loads(raw[raw.index("(") + 1:raw.rindex(")")])["stockInfo"]
    matches = [s for s in info if s["code"].lstrip("0") == code.lstrip("0")]
    if not matches:
        raise LookupError(f"no HKEX stock for code {code}")
    return matches[0]["stockId"]


def filings(sid: int, t2code: str) -> list[dict]:
    raw = get(f"{BASE}/search/titleSearchServlet.do?sortDir=0&sortByOptions=DateTime"
              f"&category=0&market=SEHK&stockId={sid}&documentType=-1&fromDate=20150101"
              f"&toDate=20991231&title=&searchType=1&t1code=40000&t2Gcode=-2"
              f"&t2code={t2code}&rowRange=200&lang=E")
    d = json.loads(raw)
    return json.loads(d["result"]) if isinstance(d.get("result"), str) else d.get("result", [])


def quarterly_filings(sid: int) -> list[dict]:
    """Quarterly reports live outside t1=40000 — find them by title search."""
    raw = get(f"{BASE}/search/titleSearchServlet.do?sortDir=0&sortByOptions=DateTime"
              f"&category=0&market=SEHK&stockId={sid}&documentType=-1&fromDate=20150101"
              f"&toDate=20991231&title=quarterly&searchType=1&t1code=-2&t2Gcode=-2"
              f"&t2code=-2&rowRange=200&lang=E")
    d = json.loads(raw)
    return json.loads(d["result"]) if isinstance(d.get("result"), str) else d.get("result", [])


def title_year(title: str) -> int:
    years = [int(y) for y in re.findall(r"(20\d\d)", title)]
    # "2024/25 annual report" style: second part is 2-digit
    frac = re.search(r"20(\d\d)\s*/\s*(\d\d)", title)
    if frac:
        years.append(2000 + int(frac[2]))
    return max(years, default=0)


def main() -> int:
    ticker = sys.argv[1]
    args = sys.argv[2:]
    dest = Path(args[args.index("--dest") + 1])
    after = int(args[args.index("--after-year") + 1]) if "--after-year" in args else 0
    code = ticker.split(".")[0]

    sid = stock_id(code)
    plan = []  # (filename, url, year)
    seen_periods = set()

    for t2code, typ in (("40100", "Annual"), ("40200", "HalfYear")):
        for f in filings(sid, t2code):
            title = f.get("TITLE", "")
            tl = title.lower()
            if "annual report" not in tl and "interim report" not in tl:
                continue  # sustainability/ESG/etc share the category
            if any(w in tl for w in ("printed version", "summary", "sustainability", "esg")):
                continue
            year = title_year(title)
            if not year or (after and year <= after):
                continue
            period = f"FY{year}" if typ == "Annual" else f"H1-{year}"
            if (typ, period) in seen_periods:
                continue  # keep newest posting only (results are date-sorted)
            seen_periods.add((typ, period))
            plan.append((f"{ticker}_{typ}_{period}.pdf", BASE + f["FILE_LINK"], year))

    q_map = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
    for f in quarterly_filings(sid):
        title = f.get("TITLE", "")
        m = re.search(r"(first|second|third|fourth) quarterly report", title.lower())
        year = title_year(title)
        if not m or not year or (after and year <= after):
            continue
        period = f"{q_map[m[1]]}-{year}"
        if ("Quarterly", period) in seen_periods:
            continue
        seen_periods.add(("Quarterly", period))
        plan.append((f"{ticker}_Quarterly_{period}.pdf", BASE + f["FILE_LINK"], year))

    if not after:  # seeding: cap volume
        annuals = [p for p in plan if "_Annual_" in p[0]][:SEED_ANNUALS]
        interims = [p for p in plan if "_HalfYear_" in p[0]][:SEED_INTERIMS]
        quarterlies = [p for p in plan if "_Quarterly_" in p[0]][:SEED_QUARTERLIES]
        plan = annuals + interims + quarterlies

    if not plan:
        print(f"hkex-adapter: nothing newer than {after} for {ticker}")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    ok = 0
    for name, url, _ in sorted(plan, key=lambda p: -p[2]):
        out = dest / name
        try:
            subprocess.run(["curl", "-sfL", "--max-time", "300", url,
                            "-H", f"User-Agent: {UA}", "-o", str(out)],
                           timeout=320, check=True)
            print(f"hkex-adapter: downloaded {name}")
            ok += 1
        except Exception as e:
            print(f"hkex-adapter: FAILED {name}: {e}")
            out.unlink(missing_ok=True)
    print(f"hkex-adapter: {ok}/{len(plan)} downloaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
