#!/usr/bin/env python3
"""Resolve a ticker's company name. Prints exactly one line: the name, or the
ticker itself when unresolvable (callers rely on this contract — the value
feeds the fetch prompt and gate.py's skip logic).

Precedence: research/{T}/info.json  >  state/companies.json  >  Yahoo  >  stub.
The winning name is synced into companies.json non-destructively (name key
updated in place; other fields untouched) so every companies.json reader sees
curated names without changes. On total failure a needs_review stub info.json
is written for the curation queue (see needs_review.py).
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from company_info import load, write  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
COMPANIES = REPO / "state" / "companies.json"

ticker = sys.argv[1]
companies = json.loads(COMPANIES.read_text())
entry = companies.get(ticker, {})

info_name = load(ticker).get("name", "")
name = info_name or entry.get("name", "")
if name == ticker:
    name = ""

if not name:
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", "10",
             f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
             "-H", "User-Agent: Mozilla/5.0"],
            capture_output=True, text=True, timeout=15).stdout
        meta = json.loads(out)["chart"]["result"][0]["meta"]
        name = meta.get("longName") or meta.get("shortName") or ""
    except Exception:
        name = ""
    if not name:
        # unresolvable: flag for strong-model curation, keep the old contract
        write(ticker, {"name": "", "needs_review": True,
                       "needs_review_reason": "yahoo-resolution-failed",
                       "updated_by": "resolve_name.py"})

if name and entry.get("name") != name:
    entry = companies.setdefault(ticker, {})
    entry["name"] = name
    entry.setdefault("sector", "Unknown")
    COMPANIES.write_text(json.dumps(companies, indent=2, sort_keys=True) + "\n")

print(name or ticker)
