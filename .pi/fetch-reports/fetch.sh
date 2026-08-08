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

# Resolve the company name deterministically (Yahoo) and persist it — LSE codes
# like BNZL are not guessable from the ticker, and this also arms gate.py's
# company-name check for this run.

finish() {
  printf '%s\t%s\n' "$TICKER" "$(date +%FT%T)" >> "$HERE/logs/attempts.tsv"
  local learned_url=""
  [ -f "$STAGING/ir_url.txt" ] && learned_url=$(head -1 "$STAGING/ir_url.txt")
  echo "--- gate ---"
  local gate_out
  gate_out=$(python3 "$HERE/gate.py" "$TICKER")
  echo "$gate_out"
  # Trust-gated source learning: a gate-promoted run is the strong signal and
  # may overwrite. A NOTHING-NEW run still located the IR page; keep that as
  # an unverified hint, but never let it replace a gate-promoted URL.
  if [ -n "$learned_url" ]; then
    local promoted=0
    echo "$gate_out" | grep -q "\[promoted\]" && promoted=1
    LEARNED_URL="$learned_url" PROMOTED="$promoted" python3 -c "
import os, sys; sys.path.insert(0, '$HERE')
from company_info import load, write
url = os.environ['LEARNED_URL'].strip()
if url.startswith('http') and len(url) < 500:
    if os.environ['PROMOTED'] == '1':
        write('$TICKER', {'ir_url': url, 'ir_url_source': 'gate-promoted',
                          'updated_by': 'fetcher-learned'})
        print(f'learned ir_url: {url}')
    else:
        have = load('$TICKER')
        # Legacy entries predate ir_url_source but were all gate-promoted.
        if not have.get('ir_url') or have.get('ir_url_source') == 'observed':
            write('$TICKER', {'ir_url': url, 'ir_url_source': 'observed',
                              'updated_by': 'fetcher-observed'})
            print(f'observed ir_url (unverified): {url}')"
  fi
  rm -f "$STAGING/ir_url.txt"
  # Commit this ticker's extracted text + name registry. Uses the repo's mkdir
  # spinlock convention (scripts/lib.sh) so concurrent research runs are safe.
  local lockdir="$REPO/state/git.lock.d" waited=0
  while ! mkdir "$lockdir" 2>/dev/null; do
    sleep 2; waited=$((waited + 2))
    [ "$waited" -gt 120 ] && { echo "git lock busy — skipping commit for $TICKER"; return 0; }
  done
  ( cd "$REPO" &&
    { git add -A -- "research/$TICKER/Extracted" 2>/dev/null || true; } &&
    { git add -- state/companies.json 2>/dev/null || true; } &&
    { git add -- "research/$TICKER/info.json" 2>/dev/null || true; } &&
    if ! git diff --cached --quiet; then
      git commit --quiet -m "chore: fetch filings for $TICKER

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
      echo "committed: fetch filings for $TICKER"
    fi )
  rmdir "$lockdir" 2>/dev/null
}

COMPANY=$(python3 "$HERE/resolve_name.py" "$TICKER")
echo "company: $COMPANY"

INVENTORY=$(python3 "$HERE/missing.py" "$TICKER")

# NZX adapter: update-mode only (current-year announcements are hydrated in
# the page; back-years are not reachable deterministically). Authoritative
# for updates; seeds fall through to archive/model.
if [[ "$TICKER" == *.NZ ]]; then
  AFTER_YEAR=$(python3 -c "import sys; sys.path.insert(0,'$HERE'); from missing import scan; print(scan('$TICKER')['newest_year'])")
  # Thin holdings (<3 filings) get the full deterministic backfill: seed mode
  # walks the per-year announcement listings and skips periods already held.
  N_EXT=$(ls "$REPO/research/$TICKER/Extracted"/*.txt 2>/dev/null | wc -l | tr -d ' ')
  [ "$N_EXT" -lt 3 ] && AFTER_YEAR=0
  if python3 "$HERE/adapters/nzx.py" "$TICKER" --dest "$STAGING" --after-year "$AFTER_YEAR"; then
    STAGED=$(ls "$STAGING"/*.pdf 2>/dev/null | wc -l | tr -d " ")
    if [ "$STAGED" -ge 1 ] || [ "$AFTER_YEAR" -gt 0 ]; then
      finish
      exit 0
    fi
    echo "nzx-adapter: empty seed bootstrap — falling back to archive/model"
  fi
fi

# ASX registry adapter: authoritative for updates; on an empty seed result we
# fall through to the AnnualReports/model path below.
if [[ "$TICKER" == *.AX ]]; then
  AFTER_YEAR=$(python3 -c "import sys; sys.path.insert(0,'$HERE'); from missing import scan; print(scan('$TICKER')['newest_year'])")
  if python3 "$HERE/adapters/asx.py" "$TICKER" --dest "$STAGING" --after-year "$AFTER_YEAR"; then
    STAGED=$(ls "$STAGING"/*.pdf 2>/dev/null | wc -l | tr -d " ")
    if [ "$STAGED" -ge 1 ] || [ "$AFTER_YEAR" -gt 0 ]; then
      finish
      exit 0
    fi
    echo "asx-adapter: empty seed — falling back to archive/model"
  fi
fi

# Deterministic exchange adapters get first crack; the model is only the
# fallback for exchanges without one.
case "$TICKER" in
  *.L|*.AX|*.NZ)
    # Seeds get the deterministic annuals backbone from AnnualReports.com when
    # possible (zero wrong-company risk); the model still handles updates and
    # interims. Fall through to the model when the archive has little.
    if echo "$INVENTORY" | grep -q "no filings"; then
      python3 "$HERE/adapters/annualreports.py" "$TICKER" --dest "$STAGING" || true
      STAGED=$(ls "$STAGING"/*.pdf 2>/dev/null | wc -l | tr -d " ")
      if [ "$STAGED" -ge 3 ]; then
        finish
        exit 0
      fi
      echo "annualreports-adapter: only $STAGED files — falling back to the model (staged files kept)"
    fi
    ;;
  *.HK)
    AFTER_YEAR=$(python3 -c "import sys; sys.path.insert(0,'$HERE'); from missing import scan; print(scan('$TICKER')['newest_year'])")
    if python3 "$HERE/adapters/hkex.py" "$TICKER" --dest "$STAGING" --after-year "$AFTER_YEAR"; then
      finish
      exit 0
    fi
    echo "hkex-adapter failed — falling back to the model"
    ;;
esac
QUIRK=$(python3 "$HERE/company_info.py" quirks "$TICKER")

cd "$STAGING"   # bare-filename downloads land in staging by construction
# perl alarm = per-ticker watchdog: a hung tool call (e.g. a filesystem-wide
# find) kills this ticker after 15 min instead of wedging the whole loop.
{
/usr/bin/perl -e 'alarm shift; exec @ARGV' 900 \
pi --mode json -p --no-session --provider ollama --model "gpt-oss-20b-64k:latest" \
  --tools read,bash,write,ls,find,grep,web_search,fetch_content \
  "Find and download newer financial reports for $COMPANY (ticker $TICKER). Search by the company name, not the ticker symbol.

What we already hold (do NOT re-download these or anything older):
$INVENTORY

Source notes: ${QUIRK:-none}

$(if echo "$INVENTORY" | grep -q "no filings"; then cat <<SEED
Task (initial seeding — we hold nothing for this company):
1. Use web_search to find the investor-relations / results page of $COMPANY.
2. Download the annual report PDF for each of the last 8 fiscal years (or as many as are published), plus the most recent half-year or quarterly report, with bash + curl (browser User-Agent, follow redirects, ALWAYS pass --max-time 120 so a hanging server cannot stall you) into the CURRENT working directory (you are already in the correct staging directory — use './filename.pdf')
3. Name each file exactly: ${TICKER}_{Type}_{Period}.pdf where Type is Annual, HalfYear, Quarterly, or Presentation and Period is like FY2024 or H1-2026 (fiscal year labels, four-digit years).
SEED
else cat <<UPDATE
Task:
1. Use web_search to find the investor-relations / results page of $COMPANY and check whether any annual or half-year/quarterly report NEWER than the newest period listed above has been published.
2. If yes, download each new report PDF with bash + curl (browser User-Agent, follow redirects, ALWAYS pass --max-time 120 so a hanging server cannot stall you) into the CURRENT working directory (you are already in the correct staging directory — use './filename.pdf')
3. Name each file exactly: ${TICKER}_{Type}_{Period}.pdf where Type is Annual, HalfYear, Quarterly, or Presentation, and Period follows the same style as the periods listed above (e.g. FY2026, H1-2026).
UPDATE
fi)
4. Verify each download is a real PDF with the file command; delete anything that is not. Also verify it is a report OF $COMPANY — if the PDF is about a different company, delete it.
5. If nothing suitable is published, download nothing — that is a fine outcome. Say NOTHING-NEW.
6. Write the URL of the investor-relations / reports page you actually used into a plain-text file named ir_url.txt in the current directory (one line, the URL only).
Strategy (do it the cheap way): use AT MOST 1-2 web searches — just enough to find the company's investor/reports page — then use fetch_content on that page to enumerate ALL report links in one pass. NEVER run one search per year. If report URLs follow an obvious pattern (e.g. .../Annual-Report-2024.pdf), derive the other years from the pattern and check each with curl -I instead of searching. Download files one at a time with individual curl commands — no multi-URL bash scripts with set -e (one bad URL kills the whole batch).
Rules: you are already in the correct working directory — use RELATIVE paths only ('.', './file.pdf') for every file operation; never use absolute paths and never list or search any directory outside the current one. Never run make, git, claude, or a nested pi. Finish by listing the files you downloaded (or NOTHING-NEW)." \
  2>&1
} 2>/dev/null | python3 "$HERE/pi_progress.py"
if [ "${PIPESTATUS[0]}" -eq 142 ]; then
  echo "watchdog: pi killed after 15 min on $TICKER — will be retried on a later pass"
fi

for stray in "$REPO/$TICKER"_*.pdf; do   # rescue anything written outside staging
  [ -f "$stray" ] && mv "$stray" "$STAGING/"
done

finish
