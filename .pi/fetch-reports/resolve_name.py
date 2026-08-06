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

def _curl(url, extra=None):
    cmd = ["curl", "-s", "--max-time", "10", url, "-H", "User-Agent: Mozilla/5.0"]
    if extra:
        cmd += extra
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout


def yahoo_chart(t):
    meta = json.loads(_curl(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}"))["chart"]["result"][0]["meta"]
    return meta.get("longName") or meta.get("shortName") or ""


def yahoo_search(t):
    d = json.loads(_curl(f"https://query1.finance.yahoo.com/v1/finance/search?q={t}&quotesCount=3"))
    qs = [q for q in d.get("quotes", []) if q.get("symbol", "").upper() == t.upper()]
    return (qs[0].get("longname") or qs[0].get("shortname") or "") if qs else ""


def openfigi(t):
    # Bloomberg's free symbology API; names come back ALL-CAPS but the gate
    # matches case-insensitively. Keyless tier is rate-limited — last resort.
    exch = {"HK": "HK", "AX": "AU", "L": "LN", "NZ": "NZ"}.get(t.rsplit(".", 1)[-1])
    if not exch or "." not in t:
        return ""
    code = t.split(".")[0].lstrip("0") or t.split(".")[0]
    body = json.dumps([{"idType": "TICKER", "idValue": code, "exchCode": exch}])
    d = json.loads(_curl("https://api.openfigi.com/v3/mapping",
                         ["-H", "Content-Type: application/json", "-d", body]))
    data = (d[0] or {}).get("data") if isinstance(d, list) and d else None
    return data[0].get("name", "").title() if data else ""


if not name:
    for source in (yahoo_chart, yahoo_search, openfigi):
        try:
            name = source(ticker)
        except Exception:
            name = ""
        if name:
            break
    if not name:
        # unresolvable: flag for strong-model curation, keep the old contract
        write(ticker, {"name": "", "needs_review": True,
                       "needs_review_reason": "auto-resolution-failed (yahoo chart+search, openfigi)",
                       "updated_by": "resolve_name.py"})

if name and entry.get("name") != name:
    entry = companies.setdefault(ticker, {})
    entry["name"] = name
    entry.setdefault("sector", "Unknown")
    COMPANIES.write_text(json.dumps(companies, indent=2, sort_keys=True) + "\n")

print(name or ticker)
