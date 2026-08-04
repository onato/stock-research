#!/bin/bash
# Run the gated fetcher over multiple tickers, sequentially (keeps the model hot).
# Usage: run.sh TICKER [TICKER...]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
for t in "$@"; do
  echo "=============== $t ==============="
  "$HERE/fetch.sh" "$t"
done
echo "=============== summary ==============="
tail -n 50 -q "$HERE"/logs/*.jsonl 2>/dev/null | python3 -c "
import json,sys
rows=[json.loads(l) for l in sys.stdin]
p=[r for r in rows if r['verdict']=='promoted']
q=[r for r in rows if r['verdict']=='quarantined']
print(f'{len(p)} promoted, {len(q)} quarantined (all-time tail)')"
