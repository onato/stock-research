#!/usr/bin/env python3
"""Curation queue: tickers whose metadata needs a strong model's attention.

Lists (a) research/*/info.json files flagged needs_review, and (b) tickers
whose most recent gate verdict was a company-name-missing quarantine and which
have no curated info.json. Read-only.

Curation workflow: for each listed ticker, verify the real company name (and
ideally the IR/reports URL), then:
  python3 company_info.py set TICKER name "Real Company Name"
  python3 company_info.py set TICKER ir_url "https://..."
  python3 company_info.py set TICKER needs_review false
resolve_name.py will sync the name into state/companies.json on next touch.

Note: legacy dirs named "{TICKER} - {Name}" (e.g. "research/NOV - Novo
Nordisk") predate this scheme and are outside the fetcher's ticker regex —
ignored here deliberately.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TICKER_RE = re.compile(r"^[A-Z0-9]+(\.[A-Z]+)?$")

flagged = []
for f in sorted((REPO / "research").glob("*/info.json")):
    ticker = f.parent.name
    if not TICKER_RE.match(ticker):
        continue
    try:
        info = json.loads(f.read_text())
    except json.JSONDecodeError:
        flagged.append((ticker, "info.json unparseable"))
        continue
    if info.get("needs_review"):
        flagged.append((ticker, info.get("needs_review_reason", "flagged")))

name_missing = []
for f in sorted((HERE / "logs").glob("*.jsonl")):
    ticker = f.stem
    if not TICKER_RE.match(ticker):
        continue
    last = {}
    for line in f.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        last[r["file"]] = r
    bad = [r for r in last.values()
           if r["verdict"] == "quarantined" and r.get("reason", "").startswith("company-name-missing")]
    if bad:
        info = {}
        p = REPO / "research" / ticker / "info.json"
        if p.exists():
            try:
                info = json.loads(p.read_text())
            except json.JSONDecodeError:
                pass
        if info.get("needs_review") is False:
            continue
        if info.get("updated_by") not in ("claude", "human", "manual"):
            name_missing.append((ticker, f"{len(bad)} name-missing quarantine(s)"))

# Repeat-empty tickers: attempted 2+ times, never promoted a single file, and
# not yet human/claude-curated — prime candidates for ir_url + quirks curation
# (the CEN.NZ pattern: JS-rendered reports pages invisible to curl).
from collections import Counter
attempts = Counter()
tsv = HERE / "logs" / "attempts.tsv"
if tsv.exists():
    for line in tsv.read_text().splitlines():
        t = line.split("\t")[0]
        if TICKER_RE.match(t):
            attempts[t] += 1
promoted_ever = set()
for f in (HERE / "logs").glob("*.jsonl"):
    for line in f.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("verdict") == "promoted":
            promoted_ever.add(f.stem)
repeat_empty = []
for t, n in sorted(attempts.items(), key=lambda kv: -kv[1]):
    if n < 2 or t in promoted_ever:
        continue
    info = json.loads((REPO / "research" / t / "info.json").read_text()) if (REPO / "research" / t / "info.json").exists() else {}
    if info.get("needs_review") is False or info.get("updated_by") in ("claude", "human", "manual") or info.get("ir_url"):
        continue
    if not (REPO / "research" / t / "PDFs").is_dir() or not any((REPO / "research" / t / "PDFs").iterdir()):
        repeat_empty.append((t, n))

if not flagged and not name_missing and not repeat_empty:
    print("curation queue empty")
else:
    for t, why in flagged:
        print(f"REVIEW  {t}: {why}")
    for t, why in name_missing:
        print(f"CHECK   {t}: {why}")
    for t, n in repeat_empty[:15]:
        print(f"EMPTY   {t}: {n} attempts, 0 promotions ever — curate ir_url/quirks")
