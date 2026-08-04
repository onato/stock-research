#!/usr/bin/env python3
"""Print the next N unresearched, fetcher-eligible tickers from the queue.

Reuses select_ticker.py's priority-ordered queue walk. Only suffixed
international tickers are eligible — US/ADR bare symbols go through EDGAR in
the main pipeline, and the fetcher's gates are weakest exactly where a model
is least needed.

Usage: next_new.py [N]
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from select_ticker import pick_new  # noqa: E402

ELIGIBLE = re.compile(r"^[A-Z0-9]+\.(NZ|L|HK|AX)$")

n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
exclude: list[str] = []
found: list[str] = []
while len(found) < n:
    t = pick_new(exclude=exclude)
    if t is None:
        break
    exclude.append(t)
    if ELIGIBLE.match(t):
        found.append(t)
print("\n".join(found))
