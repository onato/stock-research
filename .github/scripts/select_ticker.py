#!/usr/bin/env python3
"""Pick the next ticker for the automated screener to research.

Policy: new tickers first, then refresh the stalest.

1. Walk the committed queue files in `.github/queue/` in priority order and
   return the first ticker that has no `{TICKER}/Reports/` content yet.
2. Once every queued ticker has a report, fall back to refreshing the
   existing ticker whose DCF `valuation_date` is oldest.

Writes `ticker=<T>` and `mode=<new|refresh|none>` to $GITHUB_OUTPUT so the
workflow can branch on the result. Emits an empty `ticker=` when there is
nothing to do, which the workflow treats as a clean no-op.
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE_DIR = REPO_ROOT / ".github" / "queue"

# Reuse the date parsing already used by the screen-investments skill so
# staleness here means the same thing it does there.
sys.path.insert(0, str(REPO_ROOT / ".claude" / "skills" / "screen-investments"))
try:
    from screen import parse_date
except ImportError:  # pragma: no cover - keeps the selector usable standalone
    import datetime as _dt

    def parse_date(s):
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m"):
            try:
                return _dt.datetime.strptime(s[:10], fmt).date()
            except (ValueError, TypeError):
                continue
        return None


# Queue files are consumed in this order. Anything else in the directory is
# appended alphabetically, so dropping in a new exchange file works without
# editing this list.
PRIORITY = ["priority.txt", "nzx.txt", "asx.txt", "us_major.txt"]


def queue_files():
    if not QUEUE_DIR.is_dir():
        return []
    present = {p.name: p for p in QUEUE_DIR.glob("*.txt")}
    ordered = [present.pop(n) for n in PRIORITY if n in present]
    ordered.extend(present[n] for n in sorted(present))
    return ordered


def read_tickers(path):
    """One ticker per line. Blank lines and `#` comments are ignored."""
    out = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def has_reports(ticker):
    """A ticker counts as researched once its Reports/ dir has content.

    An empty Reports/ dir means a previous run died partway through, so it
    stays eligible rather than being skipped forever.
    """
    reports = REPO_ROOT / ticker / "Reports"
    return reports.is_dir() and any(reports.iterdir())


def pick_new(exclude=()):
    exclude = set(exclude)
    seen = set()
    for qf in queue_files():
        for ticker in read_tickers(qf):
            if ticker in seen or ticker in exclude:
                continue
            seen.add(ticker)
            if not has_reports(ticker):
                return ticker
    return None


def pick_stalest(exclude=()):
    """Oldest DCF by its internal `valuation_date`.

    Deliberately not file mtime: actions/checkout stamps every file with the
    checkout time, which would make an mtime ranking arbitrary in CI. A
    missing or unparseable date sorts first so those get refreshed soonest.
    """
    exclude = set(exclude)
    candidates = []
    for dcf in REPO_ROOT.glob("*/Reports/*_DCF.json"):
        ticker = dcf.parent.parent.name
        if ticker in exclude:
            continue
        try:
            data = json.loads(dcf.read_text())
        except (json.JSONDecodeError, OSError):
            candidates.append((0, None, ticker))
            continue
        d = parse_date(data.get("valuation_date"))
        # (has_date, date) sorts undated entries ahead of every dated one.
        candidates.append((1, d, ticker) if d else (0, None, ticker))

    if not candidates:
        return None
    # Ties break alphabetically for determinism across runs.
    candidates.sort(key=lambda c: (c[0], c[1].toordinal() if c[1] else 0, c[2]))
    return candidates[0][2]


def emit(ticker, mode):
    out = os.environ.get("GITHUB_OUTPUT")
    payload = f"ticker={ticker or ''}\nmode={mode}\n"
    if out:
        with open(out, "a") as fh:
            fh.write(payload)
    print(payload, end="")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--override", default="", help="Force this ticker (manual dispatch)")
    ap.add_argument(
        "--exclude",
        default="",
        help=(
            "Comma-separated tickers to skip. Used when reserving a batch for "
            "parallel runs: an empty Reports/ dir does not mark a ticker as "
            "taken (that case means 'a previous run died, retry it'), so the "
            "caller must say which names it has already handed out."
        ),
    )
    args = ap.parse_args()

    exclude = {t.strip() for t in args.exclude.split(",") if t.strip()}

    override = args.override.strip()
    if override:
        mode = "refresh" if has_reports(override) else "new"
        emit(override, mode)
        print(f"Override: {override} ({mode})", file=sys.stderr)
        return 0

    ticker = pick_new(exclude)
    if ticker:
        emit(ticker, "new")
        print(f"Selected NEW ticker: {ticker}", file=sys.stderr)
        return 0

    ticker = pick_stalest(exclude)
    if ticker:
        emit(ticker, "refresh")
        print(f"Queue exhausted; refreshing stalest: {ticker}", file=sys.stderr)
        return 0

    emit("", "none")
    print("Nothing to do: queue empty and no existing DCFs.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
