#!/usr/bin/env python3
"""Pick the next ticker for the automated screener to research.

Policy: new tickers first, then refresh the stalest.

1. Walk the committed queue files in `queue/` in priority order and
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
from collections.abc import Iterable
from pathlib import Path

import refresh_plan
import sync_portfolio_queue

REPO_ROOT = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO_ROOT / "queue"
# The live source behind priority.txt. Read directly when present (local
# runs) so a new position is queued the moment it is bought; the committed
# file is only the fallback for CI, where the sibling repo is absent.
# None = derive from REPO_ROOT at call time (so tests that retarget
# REPO_ROOT never see the real tracker); set to pin an explicit path.
PORTFOLIO_JSON: Path | None = None
PRIORITY_FILE = "priority.txt"

# Reuse the date parsing already used by the screen-investments skill so
# staleness here means the same thing it does there.
sys.path.insert(0, str(REPO_ROOT / ".claude" / "skills" / "screen-investments"))
try:
    from screen import parse_date
except ImportError:  # pragma: no cover - keeps the selector usable standalone
    import datetime as _dt

    def parse_date(s: str | None) -> "_dt.date | None":
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
# Queue files are consumed in this order. `priority.txt` holds current
# holdings and the watchlist, so those stay ahead of any broad sweep.
# Home markets come next (most familiar, smallest, easiest to verify),
# then the larger international lists. Anything not named here is
# appended alphabetically, so dropping in a new file just works.
PRIORITY = [
    "priority.txt",     # portfolio + watchlist
    "eu_priority.txt",  # hand-screened EU candidates (curated, not a sweep)
    "nzx.txt", "asx.txt",
    "us_major.txt", "adr.txt",
    "ftse.txt", "tsx.txt",
    "hsci.txt", "sti.txt",
    "dax.txt", "cac.txt",
    "nikkei.txt", "nifty.txt",
]


def queue_files() -> list[Path]:
    if not QUEUE_DIR.is_dir():
        return []
    present = {p.name: p for p in QUEUE_DIR.glob("*.txt")}
    ordered = [present.pop(n) for n in PRIORITY if n in present]
    ordered.extend(present[n] for n in sorted(present))
    return ordered


def read_tickers(path: Path) -> list[str]:
    """One ticker per line. Blank lines and `#` comments are ignored."""
    out = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def has_reports(ticker: str) -> bool:
    """A ticker counts as researched once its Reports/ dir has content.

    An empty Reports/ dir means a previous run died partway through, so it
    stays eligible rather than being skipped forever.
    """
    reports = REPO_ROOT / "research" / ticker / "Reports"
    return reports.is_dir() and any(reports.iterdir())


def portfolio_path() -> Path:
    """PORTFOLIO_JSON env var, else the module override, else the
    sibling-repo default next to REPO_ROOT."""
    env = os.environ.get("PORTFOLIO_JSON")
    if env:
        return Path(env)
    if PORTFOLIO_JSON is not None:
        return Path(PORTFOLIO_JSON)
    return (Path(REPO_ROOT).parent / "portfolio-tracker" / "data"
            / "user_portfolio.json")


def priority_tickers() -> list[str] | None:
    """Holdings then watchlist from the live tracker; None when absent.

    Never merged with priority.txt: a stale committed file would re-queue a
    position that has since been sold.
    """
    tagged = sync_portfolio_queue.portfolio_tickers(portfolio_path())
    return None if tagged is None else [t for t, _ in tagged]


def queue_sources() -> list[tuple[str, list[str]]]:
    """(name, tickers) in consumption order, with the live portfolio
    standing in for priority.txt when it is available."""
    live = priority_tickers()
    out: list[tuple[str, list[str]]] = []
    if live is not None:
        out.append((PRIORITY_FILE, live))
    for qf in queue_files():
        if qf.name == PRIORITY_FILE and live is not None:
            continue
        out.append((qf.name, read_tickers(qf)))
    return out


def pick_new(exclude: Iterable[str] = ()) -> str | None:
    exclude = set(exclude)
    for _, tickers in queue_sources():
        for ticker in tickers:
            if ticker in exclude:
                continue
            if not has_reports(ticker):
                return ticker
    return None


def pick_stalest(exclude: Iterable[str] = (), *,
                 require_new_filings: bool = False) -> str | None:
    """Oldest DCF by its internal `valuation_date`.

    Deliberately not file mtime: actions/checkout stamps every file with the
    checkout time, which would make an mtime ranking arbitrary in CI. A
    missing or unparseable date sorts first so those get refreshed soonest.

    `require_new_filings` restricts the pick to tickers with a filing the CSV
    has not absorbed. Age alone is a poor proxy for needing the parser: of 20
    tickers stale by valuation_date, 19 had no unparsed filing, so refreshing
    them re-derives identical financials at ~$6 each. Off by default.
    """
    exclude = set(exclude)
    candidates = []
    for dcf in REPO_ROOT.glob("research/*/Reports/*_DCF.json"):
        ticker = dcf.parent.parent.name
        if ticker in exclude:
            continue
        if require_new_filings and not refresh_plan.has_new_filings(
                REPO_ROOT, ticker):
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


def emit(ticker: str | None, mode: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    payload = f"ticker={ticker or ''}\nmode={mode}\n"
    if out:
        with open(out, "a") as fh:
            fh.write(payload)
    print(payload, end="")


def emit_batch(tickers: list[str], mode: str) -> None:
    """One `ticker=` line per pick, then a single `mode=`.

    Same shape as emit() for a single ticker, so a caller reading
    `grep '^ticker='` works either way.
    """
    out = os.environ.get("GITHUB_OUTPUT")
    payload = "".join(f"ticker={t}\n" for t in tickers) or "ticker=\n"
    payload += f"mode={mode}\n"
    if out:
        with open(out, "a") as fh:
            fh.write(payload)
    print(payload, end="")


def pick_batch(count: int, exclude: Iterable[str] = ()) -> list[str]:
    """Up to `count` tickers, new ones first then the stalest refreshes.

    Exists so a caller can reserve a whole batch in one process. run_loop
    used to call this script once per ticker, each spawning `uv run python3`;
    with no -n that enumerated all 1748 unresearched tickers, roughly two
    minutes of silence before any output -- which reads as a hang.
    """
    taken = set(exclude)
    picked: list[str] = []
    for chooser in (pick_new, pick_stalest):
        while len(picked) < count:
            ticker = chooser(taken)
            if not ticker:
                break            # this source is exhausted; try the next
            taken.add(ticker)
            picked.append(ticker)
        if len(picked) >= count:
            break
    return picked


def main() -> int:
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
    ap.add_argument(
        "--count", type=int, default=1,
        help="Reserve this many tickers in one call, new ones first.",
    )
    args = ap.parse_args()

    exclude = {t.strip() for t in args.exclude.split(",") if t.strip()}

    override = args.override.strip()
    if args.count > 1 and not override:
        picked = pick_batch(args.count, exclude)
        if not picked:
            emit_batch([], "none")
            print("Nothing to do: queue empty and no existing DCFs.",
                  file=sys.stderr)
            return 0
        # `new` unless every pick was a refresh; the caller only uses this
        # to describe the batch, not to branch per ticker.
        mode = "new" if not has_reports(picked[0]) else "refresh"
        emit_batch(picked, mode)
        print(f"Reserved {len(picked)} ticker(s): {', '.join(picked[:8])}"
              f"{' ...' if len(picked) > 8 else ''}", file=sys.stderr)
        return 0

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
