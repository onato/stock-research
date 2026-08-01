#!/usr/bin/env python3
"""Append-only prediction ledger (eval tier 3).

Every DCF is a forecast; forecasts are scored by outcomes, and outcomes take
6-24 months to arrive. DCF.json gets overwritten each time a ticker is
re-researched, so the prediction it embodied is lost unless it is snapshotted
at production time. This ledger is that snapshot: one JSONL row per (ticker,
valuation) recording price, scenario IVs, weights, entry price, and the hash
of the agent prompts that produced it.

Rows record RAW key->value dicts (all currency variants) rather than a single
picked number: currency resolution is the scorer's job at read time. A wrong
pick baked into an append-only file could never be fixed.

Usage:
  ledger.py append TICKER [TICKER...]   # after a research run
  ledger.py backfill                    # seed from every existing DCF.json

Both are idempotent: a row whose (ticker, valuation_date, weighted IVs,
price) already appears in the ledger is skipped, so re-running backfill or
re-scoring an unchanged ticker adds nothing.
"""

import contextlib
import datetime as dt
import fcntl
import json
import sys

import dcf_fields as F

LEDGER = F.REPO / "evals" / "ledger.jsonl"
LOCK = F.REPO / "state" / "ledger.lock"


@contextlib.contextmanager
def ledger_lock():
    """Serialize appends across parallel research_one.sh runners."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def row_key(row):
    """Identity of a prediction: same ticker+date+numbers == same forecast."""
    return (
        row.get("ticker"),
        row.get("valuation_date"),
        json.dumps(row.get("weighted_iv"), sort_keys=True),
        row.get("current_price"),
    )


def existing_keys():
    keys = set()
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                with contextlib.suppress(json.JSONDecodeError):
                    keys.add(row_key(json.loads(line)))
    return keys


def build_row(ticker):
    dcf = F.load_dcf(ticker)
    if dcf is None:
        return None
    dcf_path = F.REPO / "research" / ticker / "Reports" / f"{ticker}_DCF.json"
    return {
        "logged_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticker": ticker,
        "valuation_date": dcf.get("valuation_date"),
        "valuation_model": dcf.get("valuation_model"),
        "current_price": F.num(dcf.get("current_price")),
        "iv": F.scenario_ivs(dcf),
        "weights": F.weights(dcf),
        "weighted_iv": F.weighted_ivs(dcf),
        "weighted_upside": F.weighted_upsides(dcf),
        "entry_price": F.entry_prices(dcf),
        "hurdle_rate": F.hurdle_rate(dcf),
        "dcf_mtime": dt.datetime.fromtimestamp(
            dcf_path.stat().st_mtime, dt.UTC
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agents_sha": F.agents_sha(),
        "git_head": F.git_head(),
    }


def append(tickers):
    added, skipped, missing = [], [], []
    with ledger_lock():
        keys = existing_keys()
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a") as fh:
            for t in tickers:
                row = build_row(t)
                if row is None:
                    missing.append(t)
                    continue
                if row_key(row) in keys:
                    skipped.append(t)
                    continue
                keys.add(row_key(row))
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                added.append(t)
    return added, skipped, missing


def all_tickers():
    return sorted(
        p.parent.parent.name
        for p in F.REPO.glob("research/*/Reports/*_DCF.json")
        if p.stem == f"{p.parent.parent.name}_DCF"
    )


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("append", "backfill"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if sys.argv[1] == "backfill":
        tickers = all_tickers()
    else:
        tickers = sys.argv[2:]
        if not tickers:
            print("append needs at least one TICKER", file=sys.stderr)
            return 2

    added, skipped, missing = append(tickers)
    print(f"ledger: {len(added)} added, {len(skipped)} already logged, "
          f"{len(missing)} without DCF.json")
    if added:
        print("  added:", " ".join(added))
    if missing:
        print("  no DCF:", " ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
