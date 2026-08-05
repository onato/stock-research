#!/bin/bash
# Gated report-fetcher: gpt-oss on int347 hunts for filings newer than what we
# hold; gate.py decides what actually enters research/. Model output is treated
# as untrusted.
#
# Usage: fetch.sh TICKER
set -uo pipefail
export NODE_OPTIONS="--disable-warning=ExperimentalWarning"   # silence pi's node noise in logs
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TICKER="$1"
STAGING="$HERE/staging/$TICKER"
mkdir -p "$STAGING"
rm -f "$STAGING"/*.pdf 2>/dev/null

INVENTORY=$(python3 "$HERE/missing.py" "$TICKER")
QUIRK=$(python3 -c "
import json,sys
q=json.load(open('$HERE/quirks.json'))
t='$TICKER'
notes=[q[t]] if t in q else []
if t.endswith('.NZ'): notes.append(q['_default_nz'].replace('{CODE}', t.split('.')[0]))
print(' '.join(notes))")

cd "$STAGING"   # bare-filename downloads land in staging by construction
# perl alarm = per-ticker watchdog: a hung tool call (e.g. a filesystem-wide
# find) kills this ticker after 15 min instead of wedging the whole loop.
/usr/bin/perl -e 'alarm shift; exec @ARGV' 900 \
pi -p --no-session --provider ollama --model "gpt-oss-20b-64k:latest" \
  --tools read,bash,write,ls,find,grep,web_search,fetch_content \
  "Find and download newer financial reports for $TICKER.

What we already hold (do NOT re-download these or anything older):
$INVENTORY

Source notes: ${QUIRK:-none}

$(if echo "$INVENTORY" | grep -q "no filings"; then cat <<SEED
Task (initial seeding — we hold nothing for this company):
1. Use web_search to find the company's investor-relations / results page.
2. Download the annual report PDF for each of the last 8 fiscal years (or as many as are published), plus the most recent half-year or quarterly report, with bash + curl (browser User-Agent, follow redirects) into this exact directory: $STAGING
3. Name each file exactly: ${TICKER}_{Type}_{Period}.pdf where Type is Annual, HalfYear, Quarterly, or Presentation and Period is like FY2024 or H1-2026 (fiscal year labels, four-digit years).
SEED
else cat <<UPDATE
Task:
1. Use web_search to find the company's investor-relations / results page and check whether any annual or half-year/quarterly report NEWER than the newest period listed above has been published.
2. If yes, download each new report PDF with bash + curl (browser User-Agent, follow redirects) into this exact directory: $STAGING
3. Name each file exactly: ${TICKER}_{Type}_{Period}.pdf where Type is Annual, HalfYear, Quarterly, or Presentation, and Period follows the same style as the periods listed above (e.g. FY2026, H1-2026).
UPDATE
fi)
4. Verify each download is a real PDF with the file command; delete anything that is not.
5. If nothing suitable is published, download nothing — that is a fine outcome. Say NOTHING-NEW.
Rules: write only inside $STAGING. Never run make, git, claude, or a nested pi. Finish by listing the files you downloaded (or NOTHING-NEW)." \
  2>&1 | tail -20

for stray in "$REPO/$TICKER"_*.pdf; do   # rescue anything written outside staging
  [ -f "$stray" ] && mv "$stray" "$STAGING/"
done

echo "--- gate ---"
python3 "$HERE/gate.py" "$TICKER"
