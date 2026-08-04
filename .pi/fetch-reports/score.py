#!/usr/bin/env python3
"""Score a benchmark run against the Claude-fetched ground-truth corpus.

Recall: of the real filings newer than the cutoff, how many did the model fetch
(matched on type+period)? Precision: of what it fetched, how much corresponds
to a real filing? Content check on matches: same file (sha256) or size within
3x of the ground-truth copy.

Usage: score.py TICKER CUTOFF_YEAR
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from missing import NAME_RE, period_year, scan  # noqa: E402

HERE = Path(__file__).resolve().parent


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(ticker: str, cutoff: int) -> None:
    truth_dir = Path(scan(ticker)["pdf_dir"])
    staging = HERE / "staging" / f"bench-{ticker}"

    truth = {}   # (type, period) -> path, for filings newer than cutoff
    for f in truth_dir.glob("*.pdf"):
        m = NAME_RE.match(f.name)
        if m and period_year(m["period"]) > cutoff:
            truth[(m["type"], m["period"])] = f

    staged = {}
    junk = []
    for f in staging.glob("*.pdf"):
        m = NAME_RE.match(f.name)
        (staged.__setitem__((m["type"], m["period"]), f) if m else junk.append(f.name))

    hits, extras = [], []
    for key, f in staged.items():
        if key in truth:
            t = truth[key]
            if sha(f) == sha(t):
                hits.append((key, "identical"))
            else:
                ratio = f.stat().st_size / max(t.stat().st_size, 1)
                hits.append((key, f"size-ratio {ratio:.2f}" + (" SUSPECT" if not 0.33 < ratio < 3 else "")))
        else:
            extras.append(key)
    misses = [k for k in truth if k not in staged]

    n_truth = len(truth)
    print(f"{ticker} (cutoff {cutoff}): ground truth has {n_truth} newer filings")
    print(f"  recall:    {len(hits)}/{n_truth}")
    print(f"  extras (no ground-truth match — possible junk): {len(extras)}")
    for k, note in hits:
        print(f"    HIT   {k[0]}_{k[1]}: {note}")
    for k in misses:
        print(f"    MISS  {k[0]}_{k[1]}")
    for k in extras:
        print(f"    EXTRA {k[0]}_{k[1]}")
    for j in junk:
        print(f"    JUNK  {j}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
