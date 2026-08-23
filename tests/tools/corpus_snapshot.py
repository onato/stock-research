#!/usr/bin/env python3
"""Snapshot every fact the scanner emits across the whole research corpus.

The migration gate for parser changes: take a snapshot before and after,
then diff. A refactor that claims behavior preservation must produce an
EMPTY diff; a bug-fix step's diff must be reviewed line-by-line and stay
restricted to its targeted exchange (use --suffix).

Lives outside pytest collection (no test_ prefix, tests/tools/ is not in
testpaths) because it reads the live research/ corpus and takes seconds,
not milliseconds. Snapshots go to state/ (untracked), never to git.

Usage:
  python3 tests/tools/corpus_snapshot.py --out state/facts_before.jsonl
  python3 tests/tools/corpus_snapshot.py --out state/facts_nz.jsonl --suffix NZ
  diff state/facts_before.jsonl state/facts_after.jsonl
"""

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import build_facts as bf  # noqa: E402

# context is excluded: trimming context windows would churn every line of
# the diff without any change to what the agent adjudicates on.
FIELDS = ("source_file", "line_no", "metric", "period", "value_raw",
          "units_hint", "currency", "confidence")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--suffix", default="",
                    help="Only tickers with this listing suffix (e.g. NZ, HK)")
    args = ap.parse_args()

    rows = []
    for extracted in sorted(REPO.glob("research/*/Extracted")):
        ticker = extracted.parent.name
        if " " in ticker:      # legacy dirs ("NetEase - 9999.HK") are not tickers
            continue
        suffix = ticker.rsplit(".", 1)[1] if "." in ticker else ""
        if args.suffix and suffix != args.suffix:
            continue
        files = sorted(extracted.glob("*.txt"))
        # Same drive as build_facts: interims are labelled against the
        # fiscal-year end the folder's annual reports state.
        fy_end = bf.folder_fiscal_year_end(files)
        for f in files:
            rows.extend((ticker, *[fact.get(k) for k in FIELDS])
                        for fact in bf.scan_file(f, fy_end))

    rows.sort(key=json.dumps)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
    print(f"{len(rows)} facts -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
