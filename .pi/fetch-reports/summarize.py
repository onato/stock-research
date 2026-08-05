#!/usr/bin/env python3
"""Compose a human summary of fetcher activity since a given timestamp,
from the gate's jsonl decision logs. Used for the Telegram nightly report.

Usage: summarize.py --since 2026-08-06T02:00:00
"""
import glob
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent

since = sys.argv[sys.argv.index("--since") + 1]

rows = []
for f in glob.glob(str(HERE / "logs" / "*.jsonl")):
    for line in open(f):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("ts", "") >= since:
            rows.append(r)

promoted = [r["file"] for r in rows if r["verdict"] == "promoted"]
quarantined = [(r["file"], r.get("reason", "?")) for r in rows if r["verdict"] == "quarantined"]

if not rows:
    print("No files promoted or quarantined.")
    sys.exit(0)

by_ticker = Counter(f.split("_")[0] for f in promoted)
print(f"Promoted {len(promoted)} files across {len(by_ticker)} tickers:")
for t, n in sorted(by_ticker.items()):
    files = sorted(f.split("_", 1)[1].rsplit(".", 1)[0] for f in promoted if f.startswith(t + "_"))
    listing = ", ".join(files[:4]) + ("…" if len(files) > 4 else "")
    print(f"  {t}: {listing}")

if quarantined:
    reasons = Counter(reason.split(" (")[0] for _, reason in quarantined)
    print(f"Quarantined {len(quarantined)}: " + ", ".join(f"{n}x {k}" for k, n in reasons.most_common()))
    wrong = [f for f, r in quarantined if r.startswith("wrong-company")]
    for f in wrong[:5]:
        print(f"  ⚠ {f} (wrong company)")
