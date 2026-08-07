#!/usr/bin/env python3
"""Per-exchange extraction coverage: does the parser handle each filing regime?

The queue spans NZX, ASX, HSCI, FTSE, Nikkei, TSX and ~3,500 US names, and
each exchange has its own filing conventions. Coverage was previously
inferred from whichever ticker happened to be in front of us -- which
produced two wrong conclusions in a row ("the grep loop is gone", then
"glossy layouts break it"), both generalised from a single sample.

This measures it instead. No model calls, so it is free and can run on
every commit.

Reports, per exchange:
  tickers      how many have extracted filings
  zero         how many yield NO facts at all (the failure that matters)
  coverage     % of the schema's core metrics that appear at least once
  regime       which extraction path the exchange needs

Usage:
  exchange_eval.py                 # all exchanges
  exchange_eval.py --exchange NZ   # one
  exchange_eval.py --json out.json
"""

import argparse
import collections
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import build_facts as bf

REPO = pathlib.Path(__file__).resolve().parents[1]

# Suffix -> (exchange label, whether a structured feed exists).
# Only the US and Japan publish XBRL that is free and keyless; the rest are
# PDF regimes where the text extractor is the only option.
EXCHANGES = {
    "":     ("US",  "SEC XBRL (companyfacts)"),
    "NZ":   ("NZX", "PDF only"),
    "AX":   ("ASX", "PDF only"),
    "HK":   ("HKEX", "PDF only"),
    "L":    ("LSE", "PDF (Companies House iXBRL exists, needs key)"),
    "AS":   ("Euronext AMS", "PDF only"),
    "V":    ("TSXV", "PDF only"),
    "TO":   ("TSX", "PDF only"),
    "T":    ("TSE Japan", "EDINET XBRL (needs key)"),
    "PA":   ("Euronext Paris", "PDF only"),
    "DE":   ("XETRA", "PDF only"),
    "SI":   ("SGX", "PDF only"),
    "NS":   ("NSE India", "PDF only"),
    "U":    ("US (unit)", "SEC XBRL"),
}

# A filing whose text is mostly XBRL taxonomy URLs rather than prose --
# pdftotext ran on an iXBRL document and produced machine markup.
def is_ixbrl(text: str) -> bool:
    head = text[:5000]
    return (head.count("fasb.org") + head.count("us-gaap")
            + head.count("xbrl") + head.count("ifrs-full")) > 3


def exchange_of(ticker: str) -> tuple[str, str]:
    suffix = ticker.rsplit(".", 1)[1].upper() if "." in ticker else ""
    return EXCHANGES.get(suffix, (suffix or "US", "unknown"))


def scan_ticker(ticker: str, sample: int = 6) -> dict[str, Any] | None:
    """Extraction stats for one ticker. Samples filings to stay fast."""
    d = REPO / "research" / ticker / "Extracted"
    files = sorted(d.glob("*.txt")) if d.is_dir() else []
    if not files:
        return None

    # Prefer statutory filings. Presentations and investor decks are
    # stylised slide text with no statement tables, so sampling them
    # reports a parser failure where none exists (2CC.NZ has 42 filings,
    # mostly decks). Fall back to everything only if there are no
    # statutory documents at all.
    statutory = [f for f in files
                 if any(k in f.name.lower()
                        for k in ("annual", "halfyear", "half-year", "interim",
                                  "10k", "10-k", "10q", "10-q", "20f", "results",
                                  "financial"))]
    pool = statutory or files

    # Both ends: newest filings matter most, oldest exercise layout variation.
    picked = pool[:sample // 2] + pool[-(sample - sample // 2):]
    picked = list(dict.fromkeys(picked))

    facts: list[dict[str, Any]] = []
    ixbrl = 0
    for f in picked:
        text = f.read_text(errors="replace")
        if is_ixbrl(text):
            ixbrl += 1
            continue
        facts.extend(bf.scan_file(f))

    metrics = {f["metric"] for f in facts}
    return {
        "ticker": ticker,
        "filings": len(files),
        "sampled": len(picked),
        "ixbrl": ixbrl,
        "facts": len(facts),
        "metrics_found": len(metrics),
        "metrics": sorted(metrics),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", default="", help="restrict to one, e.g. NZ")
    ap.add_argument("--json", default="")
    ap.add_argument("--verbose", action="store_true", help="list per-ticker")
    args = ap.parse_args()

    by_exch = collections.defaultdict(list)
    for d in sorted(REPO.glob("research/*/Extracted")):
        ticker = d.parent.name
        label, regime = exchange_of(ticker)
        if args.exchange and args.exchange.upper() not in (label.upper(),
                                                           ticker.rsplit(".", 1)[-1].upper()):
            continue
        st = scan_ticker(ticker)
        if st:
            st["regime"] = regime
            by_exch[label].append(st)

    if not by_exch:
        print("no extracted filings found", file=sys.stderr)
        return 1

    # The set of metrics the schema actually wants populated.
    core = set(bf.PATTERNS)

    print(f"  {'exchange':14s} {'tickers':>7s} {'zero':>5s} {'ixbrl':>6s} "
          f"{'metrics':>8s}  regime")
    print("  " + "-" * 76)
    out = {}
    for label, rows in sorted(by_exch.items(), key=lambda kv: -len(kv[1])):
        zero = sum(1 for r in rows if r["facts"] == 0 and r["ixbrl"] == 0)
        ix = sum(1 for r in rows if r["ixbrl"] > 0)
        seen = set()
        for r in rows:
            seen |= set(r["metrics"])
        pct = len(seen & core) / len(core) * 100 if core else 0
        flag = ""
        if zero:
            flag = f"  <-- {zero} ticker(s) yield NOTHING"
        print(f"  {label:14s} {len(rows):7d} {zero:5d} {ix:6d} "
              f"{pct:7.0f}%  {rows[0]['regime']}{flag}")
        out[label] = {"tickers": len(rows), "zero": zero, "ixbrl": ix,
                      "metric_coverage_pct": round(pct, 1),
                      "regime": rows[0]["regime"]}

        if args.verbose:
            for r in sorted(rows, key=lambda r: r["facts"]):
                note = "iXBRL" if r["ixbrl"] else ("ZERO" if not r["facts"] else "")
                print(f"      {r['ticker']:10s} {r['facts']:6d} facts "
                      f"{r['metrics_found']:2d} metrics  {note}")

    # Which core metrics does no exchange ever produce? Those are pattern
    # gaps rather than regime problems.
    everything = set()
    for rows in by_exch.values():
        for r in rows:
            everything |= set(r["metrics"])
    never = sorted(core - everything)
    if never:
        print(f"\n  Metrics NO ticker ever yields ({len(never)}):")
        print("    " + ", ".join(never))
        print("    -> these are build_facts.py pattern gaps, not exchange issues")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(out, indent=2) + "\n")
        print(f"\n  written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
