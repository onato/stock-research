#!/bin/bash
# Benchmark the fetcher against ground truth: pretend we only hold filings up
# to CUTOFF_YEAR, let the model hunt for "newer" ones, then score its staging
# haul against the Claude-fetched corpus we actually have. Nothing is promoted.
#
# Usage: benchmark.sh TICKER CUTOFF_YEAR
set -uo pipefail
export NODE_OPTIONS="--disable-warning=ExperimentalWarning"   # silence pi's node noise in logs
HERE="$(cd "$(dirname "$0")" && pwd)"
TICKER="$1"; CUTOFF="$2"
STAGING="$HERE/staging/bench-$TICKER"
mkdir -p "$STAGING"; rm -f "$STAGING"/*.pdf 2>/dev/null

FAKE_INVENTORY=$(python3 -c "
import sys; sys.path.insert(0, '$HERE')
from missing import scan, period_year
r = scan('$TICKER')
print(f\"{r['company']} ({r['ticker']})\")
for t, ps in sorted(r['filings'].items()):
    old = [p for p in ps if period_year(p) <= $CUTOFF]
    if old: print(f'  newest {t}: {max(old, key=period_year)}  ({len(old)} on file)')")

QUIRK=$(python3 -c "
import json
q=json.load(open('$HERE/quirks.json'))
t='$TICKER'
notes=[q[t]] if t in q else []
if t.endswith('.NZ'): notes.append(q['_default_nz'].replace('{CODE}', t.split('.')[0]))
print(' '.join(notes))")

pi -p --no-session --provider ollama --model "gpt-oss-20b-64k:latest" \
  --tools read,bash,write,ls,find,grep,web_search,fetch_content \
  "Find and download newer financial reports for $TICKER.

What we already hold (do NOT re-download these or anything older):
$FAKE_INVENTORY

Source notes: ${QUIRK:-none}

Task:
1. Use web_search to find the company's investor-relations / results page and check whether any annual or half-year/quarterly report NEWER than the newest period listed above has been published.
2. Download each new report PDF with bash + curl (browser User-Agent, follow redirects) into this exact directory: $STAGING
3. Name each file exactly: ${TICKER}_{Type}_{Period}.pdf where Type is Annual, HalfYear, Quarterly, or Presentation, and Period follows the same style as the periods listed above (e.g. FY2026, H1-2026).
4. Verify each download is a real PDF with the file command; delete anything that is not.
5. If nothing newer exists, say NOTHING-NEW.
Rules: write only inside $STAGING. Never run make, git, claude, or a nested pi. Finish by listing the files you downloaded (or NOTHING-NEW)." \
  2>&1 | tail -12

echo "--- score ---"
python3 "$HERE/score.py" "$TICKER" "$CUTOFF"
