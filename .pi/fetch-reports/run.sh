#!/bin/bash
# Run the gated fetcher over multiple tickers, sequentially (keeps the model hot).
# Every line is timestamped so a stalled ticker is visible at a glance.
# Usage: run.sh TICKER [TICKER...]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
for t in "$@"; do
  start=$(date +%s)
  echo "=============== $t — started $(date '+%T') ==============="
  "$HERE/fetch.sh" "$t" 2>&1 | perl -pe 'BEGIN { $| = 1 } my @t = localtime; $_ = sprintf("[%02d:%02d:%02d] ", $t[2], $t[1], $t[0]) . $_'
  echo "--------------- $t done in $(( $(date +%s) - start ))s ---------------"
done
echo "=============== summary $(date '+%T') ==============="
tail -n 50 -q "$HERE"/logs/*.jsonl 2>/dev/null | python3 -c "
import json,sys
rows=[json.loads(l) for l in sys.stdin]
p=[r for r in rows if r['verdict']=='promoted']
q=[r for r in rows if r['verdict']=='quarantined']
print(f'{len(p)} promoted, {len(q)} quarantined (all-time tail)')"
