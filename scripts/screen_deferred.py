#!/usr/bin/env python3
"""Put the least likely candidates on an "afterwards" list.

Every unresearched queued ticker costs the same ~$6 research run and comes up
in file order, whether it trades on a P/E of 15 or 150, whether it is
debt-free or one bad year from its covenants. This screen reads the
stockanalysis.com statistics page for each one and writes
`state/deferred.txt`: the selector (`select_ticker.pick_new`,
`filter_tickers.eligible`) researches everything else first.

Rules (agreed 2026-09-22):

    P/E            > 50     trailing; a loss-maker has no P/E and is NOT deferred
    Debt / Equity  > 2.0x   non-financials only
    Net debt/EBITDA> 5x     non-financials only; computed from net cash, the
                            site's gross Debt/EBITDA is the fallback
    Interest cover < 2x     non-financials only

DELIBERATE NON-GOALS
--------------------
It DEFERS, it never excludes. A deferred ticker is still researched once the
ordinary queue is exhausted, and `make run TICKER=X` ignores the list. The
permanent list is state/never_interested.txt, and that stays a human call.

Missing data never defers. A page with no numbers, a ratio the site prints
as n/a, an unknown sector when a debt rule would fire -- each leaves the
ticker where it was. Precision over recall, as in screen_ethics: a wrongly
deferred good company sinks to the back of a 1,400-name queue nobody re-reads.

Banks, insurers and REITs are exempt from the debt rules: a bank IS leverage.
The sector comes from info.json when the repo already knows it, otherwise
from the company page -- fetched only when a debt rule would fire, so the
extra request is spent on the minority of high-debt names.

Negative interest cover is negative EBIT -- a loss-maker, same as no P/E --
and never fires the cover rule. Negative equity never fires the D/E rule. Buyback-driven negative equity
(AutoZone, Home Depot) prints as a negative D/E; that is not too much debt,
and the net-debt and interest-cover rules still see a genuinely over-geared
company.

The crawl is the profile backfill's: same host, same pacing, same resume
state shape (state/deferred_screen.json). Run it overnight with
`scripts/backfill_profiles_loop.sh --script screen_deferred.py`.

Usage:
    screen_deferred.py --dry-run                # what the next batch would do
    screen_deferred.py --limit 150 --apply      # one batch, write info.json + deferred.txt
    screen_deferred.py --ticker BHP.AX          # one, printed, nothing written
    screen_deferred.py --status                 # progress so far
    screen_deferred.py --report                 # the afterwards list and why
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_mod
import json
import pathlib
import random
import re
import sys
import time
import urllib.error
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

import select_ticker
from backfill_profiles import (  # noqa: F401  (re-exported pacing contract)
    DELAY_SECONDS,
    EXIT_OK,
    EXIT_RATE_LIMITED,
    JITTER_SECONDS,
    MARKETS,
    MAX_CONSECUTIVE_429,
    PERMANENT,
    _git,
    backoff_seconds,
    fetch,
    load_state,
    pending,
    save_state,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "state" / "deferred_screen.json"
DEFERRED_FILE = "deferred.txt"
DEFAULT_LIMIT = 150
SOURCE = "stockanalysis.com"

# Thresholds. A value AT the threshold passes; only beyond it defers.
MAX_PE = 50.0
MAX_DE = 2.0
MAX_ND_EBITDA = 5.0
MIN_INTEREST_COVER = 2.0

# The statistics page embeds every number as
#   {id:"pe",title:"PE Ratio",value:"21.76",hover:"21.764"}
# These are the ids read. `marketcap`, `netinc` and `equity` are stored for
# the record and for later rules; nothing here defers on them.
FIELDS: tuple[str, ...] = (
    "pe", "peForward", "debtEquity", "debtEbitda", "interestCoverage",
    "netcash", "ebitda", "debt", "marketcap", "netinc", "equity",
)

# Sectors exempt from the debt rules. Matched case-insensitively against the
# site's sector names ("Financials", "Real Estate") and the curated free-text
# `info.json:sector` values ("Wealth Management / Superannuation / Banking").
FINANCIAL_RE = re.compile(
    r"financ|bank|insur|real estate|\breit\b|property|lend|deposit|"
    r"capital markets|asset manag|wealth|superannuation|investment trust",
    re.IGNORECASE)

_LITERAL_RE = re.compile(r'\{id:"(?P<id>[A-Za-z]+)",title:"[^"]*",value:"(?P<value>[^"]*)"')
_SECTOR_RE = re.compile(r'sector:\{value:"([^"]{2,60})"')
_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------
def _page_url(ticker: str, page: str) -> str | None:
    """stockanalysis.com page for a ticker, or None for an unmapped market.

    None rather than a guess: a guessed path 404s, and a 404 is banked as
    "no such company", which is a different and wrong conclusion.
    """
    if "." not in ticker:
        return f"https://stockanalysis.com/stocks/{ticker}/{page}/"
    base, suffix = ticker.rsplit(".", 1)
    market = MARKETS.get(suffix.upper())
    if not market:
        return None
    return f"https://stockanalysis.com/quote/{market}/{base}/{page}/"


def stats_url(ticker: str) -> str | None:
    return _page_url(ticker, "statistics")


def company_url(ticker: str) -> str | None:
    return _page_url(ticker, "company")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def parse_number(text: str | None) -> float | None:
    """A number as the site prints it: `21.76`, `-14.26B`, `24.00%`,
    `7,958,276`, `n/a`. Anything unreadable is None, never 0."""
    t = (text or "").strip().replace(",", "")
    if not t or t.lower() in ("n/a", "-", "--", "na"):
        return None
    mult = 1.0
    if t.endswith("%"):
        t = t[:-1]
    elif t[-1].upper() in _SUFFIX:
        mult = _SUFFIX[t[-1].upper()]
        t = t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


def parse_statistics(page: str) -> dict[str, float | None]:
    """Every FIELDS id from the embedded literals; absent or n/a is None."""
    found: dict[str, str] = {}
    for m in _LITERAL_RE.finditer(page):
        found.setdefault(m.group("id"), m.group("value"))
    return {f: parse_number(found.get(f)) for f in FIELDS}


def has_data(stats: dict[str, float | None]) -> bool:
    """A page that yielded nothing at all is a template, not a company."""
    return any(v is not None for v in stats.values())


def parse_sector(page: str) -> str | None:
    m = _SECTOR_RE.search(page)
    return html_mod.unescape(m.group(1)).strip() if m else None


def is_financial(sector: str | None) -> bool:
    return bool(sector) and bool(FINANCIAL_RE.search(sector or ""))


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Screen:
    pe: float | None
    forward_pe: float | None
    debt_equity: float | None
    net_debt_ebitda: float | None
    interest_coverage: float | None
    market_cap: float | None
    sector: str | None
    reasons: tuple[str, ...]

    @property
    def deferred(self) -> bool:
        return bool(self.reasons)


def net_debt_to_ebitda(stats: dict[str, float | None]) -> float | None:
    """Net debt / EBITDA from net cash, else the site's gross Debt/EBITDA.

    Net is fairer: a company with more cash than debt is not over-geared
    whatever its gross ratio says. Negative or missing EBITDA gives no ratio
    at all -- a loss-making company's leverage is a different question.
    """
    netcash, ebitda = stats.get("netcash"), stats.get("ebitda")
    if netcash is not None and ebitda is not None:
        return -netcash / ebitda if ebitda > 0 else None
    return stats.get("debtEbitda")


def debt_reasons(stats: dict[str, float | None]) -> tuple[str, ...]:
    """The debt rules alone, sector-blind. The crawler uses this to decide
    whether the sector is worth a second request."""
    out: list[str] = []
    de = stats.get("debtEquity")
    # Negative D/E is negative equity, not too much debt.
    if de is not None and de > MAX_DE:
        out.append(f"D/E {de:.1f}x")
    nd = net_debt_to_ebitda(stats)
    if nd is not None and nd > MAX_ND_EBITDA:
        out.append(f"ND/EBITDA {nd:.1f}x")
    cover = stats.get("interestCoverage")
    # Negative cover is negative EBIT: a loss-maker, which the P/E rule
    # already declines to defer (CXO.AX printed -21.6x with zero debt).
    if cover is not None and 0 <= cover < MIN_INTEREST_COVER:
        out.append(f"int cover {cover:.1f}x")
    return tuple(out)


def evaluate(stats: dict[str, float | None], sector: str | None) -> Screen:
    """Apply the rules. Debt rules need a known, non-financial sector."""
    reasons: list[str] = []
    pe = stats.get("pe")
    if pe is not None and pe > MAX_PE:
        reasons.append(f"P/E {pe:.1f}")
    if sector and not is_financial(sector):
        reasons.extend(debt_reasons(stats))
    return Screen(
        pe=pe,
        forward_pe=stats.get("peForward"),
        debt_equity=stats.get("debtEquity"),
        net_debt_ebitda=net_debt_to_ebitda(stats),
        interest_coverage=stats.get("interestCoverage"),
        market_cap=stats.get("marketcap"),
        sector=sector,
        reasons=tuple(reasons),
    )


# --------------------------------------------------------------------------
# Repo I/O
# --------------------------------------------------------------------------
def _read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def known_sector(info: dict[str, Any]) -> str | None:
    """The sector the repo already holds for a ticker, if any."""
    for value in (info.get("sector"), (info.get("screen") or {}).get("sector")):
        if isinstance(value, str) and value.strip() and value.lower() != "unknown":
            return value.strip()
    return None


def write_info(path: pathlib.Path, screen: Screen) -> None:
    """Merge a `screen` block into info.json, leaving every other key alone.
    Fills a missing `sector` (never overwrites a curated one)."""
    d = _read_json(path)
    block = asdict(screen)
    block["defer"] = list(block.pop("reasons"))
    block["source"] = SOURCE
    block["fetched_at"] = dt.date.today().isoformat()
    d["screen"] = block
    if screen.sector and not d.get("sector"):
        d["sector"] = screen.sector
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")


def deferred_entries(root: pathlib.Path) -> list[tuple[str, str]]:
    """(ticker, reasons) for every info.json with a non-empty screen.defer."""
    out: list[tuple[str, str]] = []
    for info in sorted(root.glob("research/*/info.json")):
        screen = _read_json(info).get("screen") or {}
        defer = screen.get("defer") if isinstance(screen, dict) else None
        if defer:
            out.append((info.parent.name, "; ".join(str(r) for r in defer)))
    return out


_HEADER = """\
# GENERATED by `make screen-deferred APPLY=1` (scripts/screen_deferred.py)
# -- do not hand-edit; the source is `screen.defer` in each ticker's
# research/{{T}}/info.json.
#
# The AFTERWARDS list: companies on a ridiculous P/E or carrying far too
# much debt. Not an exclusion -- the selector researches every other new
# ticker first, then these, then stale refreshes. Holdings and the watchlist
# (queue/priority.txt) are never deferred, and `make run TICKER=X` ignores
# this file. The permanent list is state/never_interested.txt.
#
# Rules: P/E > {pe:g}; for non-financials D/E > {de:g}x, net debt/EBITDA >
# {nd:g}x, interest cover < {ic:g}x. Data: {source} statistics pages.
#
# TICKER  reason
"""


def write_deferred(root: pathlib.Path) -> int:
    """Regenerate state/deferred.txt from the info.json blocks. Returns the
    number of tickers listed."""
    entries = deferred_entries(root)
    body = _HEADER.format(pe=MAX_PE, de=MAX_DE, nd=MAX_ND_EBITDA,
                          ic=MIN_INTEREST_COVER, source=SOURCE)
    body += "".join(f"{t}  {why}\n" for t, why in entries)
    path = root / "state" / DEFERRED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return len(entries)


def _priority(root: pathlib.Path) -> set[str]:
    """Holdings and watchlist: the committed priority file plus the live
    tracker when it sits beside this checkout."""
    out = set(select_ticker.read_tickers(root / "queue" / select_ticker.PRIORITY_FILE)) \
        if (root / "queue" / select_ticker.PRIORITY_FILE).exists() else set()
    live = root.parent / "portfolio-tracker" / "data" / "user_portfolio.json"
    tagged = select_ticker.sync_portfolio_queue.portfolio_tickers(live)
    if tagged:
        out |= {t for t, _ in tagged}
    return out


def _queued(root: pathlib.Path) -> list[str]:
    """Every queued ticker, in the order the selector consumes the files
    (select_ticker.PRIORITY, then the rest alphabetically), so the first
    batch screened is the next batch the selector would pick."""
    qdir = root / "queue"
    present = {p.name: p for p in qdir.glob("*.txt")} if qdir.is_dir() else {}
    ordered = [present.pop(n) for n in select_ticker.PRIORITY if n in present]
    ordered.extend(present[n] for n in sorted(present))
    seen: dict[str, None] = {}
    for path in ordered:
        for t in select_ticker.read_tickers(path):
            seen.setdefault(t, None)
    return list(seen)


def candidates(root: pathlib.Path = ROOT) -> list[str]:
    """Queued, unresearched, not never-interested, not held or watched."""
    never = select_ticker._ticker_file(root / "state" / "never_interested.txt")
    skip = never | _priority(root)
    out: list[str] = []
    for t in _queued(root):
        if t in skip:
            continue
        reports = root / "research" / t / "Reports"
        if reports.is_dir() and any(reports.iterdir()):
            continue
        out.append(t)
    return out


def pending_by_age(tickers: list[str], state: dict[str, dict[str, str]],
                   max_age: int | None) -> list[str]:
    """`pending`, plus done entries older than `max_age` days when given.
    Numbers drift: a P/E of 60 a year ago says little today."""
    todo = pending(tickers, state)
    if max_age is None:
        return todo
    cutoff = (dt.date.today() - dt.timedelta(days=max_age)).isoformat()
    done = state.get("done", {})
    stale = [t for t in tickers if t in done and done[t] < cutoff]
    seen = set(todo)
    return [t for t in tickers if t in seen or t in stale]


# --------------------------------------------------------------------------
# One ticker
# --------------------------------------------------------------------------
def screen_ticker(ticker: str, root: pathlib.Path = ROOT) -> Screen | None:
    """Fetch, parse and evaluate one ticker. None when the page has no data.

    Raises whatever `fetch` raises (HTTPError, URLError) so the crawl loop
    can tell a 429 from a 404 -- the two mean opposite things.
    """
    url = stats_url(ticker)
    if not url:
        return None
    stats = parse_statistics(fetch(url))
    if not has_data(stats):
        return None
    info = _read_json(root / "research" / ticker / "info.json")
    sector = known_sector(info)
    if sector is None and debt_reasons(stats):
        curl = company_url(ticker)
        if curl:
            sector = parse_sector(fetch(curl))
    return evaluate(stats, sector)


def describe(ticker: str, screen: Screen | None) -> str:
    if screen is None:
        return f"{ticker:<10} no data"
    def fmt(v: float | None, suffix: str = "") -> str:
        return "n/a" if v is None else f"{v:.1f}{suffix}"
    nums = (f"P/E {fmt(screen.pe)}  D/E {fmt(screen.debt_equity, 'x')}  "
            f"ND/EBITDA {fmt(screen.net_debt_ebitda, 'x')}  "
            f"cover {fmt(screen.interest_coverage, 'x')}  "
            f"[{screen.sector or 'sector unknown'}]")
    verdict = "DEFER " + "; ".join(screen.reasons) if screen.deferred else "ok"
    return f"{ticker:<10} {nums}  -> {verdict}"


# --------------------------------------------------------------------------
# Commit
# --------------------------------------------------------------------------
def commit_screen(paths: list[str], screened: int, deferred: int,
                  root: pathlib.Path = ROOT) -> bool:
    """Commit exactly what this run wrote: the info.json files, the state
    file and the regenerated deferred list. Never -A."""
    if not paths or screened <= 0:
        return False
    rc, _ = _git("add", *paths, "state/deferred_screen.json",
                 f"state/{DEFERRED_FILE}", root=root)
    if rc != 0:
        return False
    rc, _ = _git("diff", "--cached", "--quiet", root=root)
    if rc == 0:
        return False
    msg = (f"chore: screen {screened} queued ticker(s), defer {deferred}\n\n"
           "P/E and debt from stockanalysis.com statistics pages; the deferred\n"
           "ones are researched after the rest of the queue.\n"
           "Automated; no model, no valuation change.")
    rc, out = _git("commit", "-q", "-m", msg, root=root)
    if rc != 0:
        print(f"commit failed: {out}", file=sys.stderr)
        return False
    return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def report(root: pathlib.Path = ROOT) -> int:
    entries = deferred_entries(root)
    screened = sum(1 for p in root.glob("research/*/info.json")
                   if isinstance(_read_json(p).get("screen"), dict))
    kinds = Counter(r.split()[0] for _, why in entries for r in why.split("; "))
    print(f"screened {screened}   deferred {len(entries)}")
    for kind, n in kinds.most_common():
        print(f"  {kind:<10} {n}")
    if entries:
        print()
        for t, why in entries:
            print(f"{t:<10} {why}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"tickers to fetch this run (default {DEFAULT_LIMIT})")
    p.add_argument("--ticker", help="screen one ticker and print it")
    p.add_argument("--apply", action="store_true",
                   help="write info.json blocks and state/deferred.txt")
    p.add_argument("--dry-run", action="store_true", help="list, fetch nothing")
    p.add_argument("--status", action="store_true", help="progress so far")
    p.add_argument("--report", action="store_true",
                   help="the deferred list and why each ticker is on it")
    p.add_argument("--max-age", type=int, default=None,
                   help="re-screen tickers screened more than N days ago")
    p.add_argument("--delay", type=float, default=DELAY_SECONDS,
                   help=f"seconds between requests (default {DELAY_SECONDS})")
    p.add_argument("--commit", action="store_true",
                   help="commit what was written (for unattended runs)")
    args = p.parse_args(argv)

    if args.report:
        return report()

    if args.ticker:
        try:
            screen = screen_ticker(args.ticker)
        except urllib.error.HTTPError as e:
            print(f"{args.ticker}: HTTP {e.code}", file=sys.stderr)
            return 1
        print(describe(args.ticker, screen))
        if args.apply and screen is not None:
            write_info(ROOT / "research" / args.ticker / "info.json", screen)
            n = write_deferred(ROOT)
            print(f"wrote info.json; state/{DEFERRED_FILE} lists {n}")
        return 0

    state = load_state(STATE_PATH)
    todo = pending_by_age(candidates(), state, args.max_age)

    if args.status:
        done, failed = len(state["done"]), len(state["failed"])
        print(f"screened {done}   failed {failed}   remaining {len(todo)}")
        if failed:
            for reason, k in Counter(state["failed"].values()).most_common():
                print(f"  {reason:<14} {k}")
        if todo:
            runs = -(-len(todo) // max(args.limit, 1))
            print(f"\n~{runs} more run(s) of {args.limit} to finish")
        return 0

    batch = todo[: args.limit]
    if args.dry_run:
        for t in batch:
            print(f"{t:<12} {stats_url(t) or '(no url -- unmapped market)'}")
        print(f"\n{len(batch)} of {len(todo)} remaining "
              f"(~{args.delay + JITTER_SECONDS / 2:.0f}s each, "
              f"~{len(batch) * (args.delay + JITTER_SECONDS / 2) / 60:.0f} min)")
        return 0

    screened = deferred = failures = 0
    consecutive_429 = 0
    rate_limited = False
    written: list[str] = []
    for i, t in enumerate(batch, 1):
        if not stats_url(t):
            state["failed"][t] = "no-url"
            failures += 1
            continue
        try:
            screen = screen_ticker(t)
            if screen is None:
                state["failed"][t] = "no-profile"
                failures += 1
            else:
                if args.apply:
                    write_info(ROOT / "research" / t / "info.json", screen)
                    written.append(f"research/{t}/info.json")
                state["done"][t] = dt.date.today().isoformat()
                state["failed"].pop(t, None)
                screened += 1
                deferred += int(screen.deferred)
                print(f"[{i}/{len(batch)}] {describe(t, screen)}")
            consecutive_429 = 0
        except urllib.error.HTTPError as e:
            if e.code == 429:
                consecutive_429 += 1
                state["failed"][t] = "http-429"
                if consecutive_429 >= MAX_CONSECUTIVE_429:
                    print(f"\n{consecutive_429} rate-limits in a row -- stopping. "
                          "Rerun later; progress is saved.", file=sys.stderr)
                    rate_limited = True
                    break
                wait = backoff_seconds(consecutive_429)
                print(f"  rate-limited, sleeping {wait / 60:.0f} min", file=sys.stderr)
                if args.apply:
                    save_state(STATE_PATH, state)
                time.sleep(wait)
                continue
            state["failed"][t] = f"http-{e.code}"
            failures += 1
        except Exception as e:  # one bad ticker must not end the sweep
            state["failed"][t] = type(e).__name__
            failures += 1

        if args.apply:
            save_state(STATE_PATH, state)
        if i < len(batch):
            time.sleep(args.delay + random.uniform(0, JITTER_SECONDS))

    listed = 0
    if args.apply:
        save_state(STATE_PATH, state)
        listed = write_deferred(ROOT)
    left = len(pending_by_age(candidates(), state, args.max_age))
    print(f"\nscreened {screened}   deferred {deferred}   failed {failures}   "
          f"remaining {left}" + (f"   state/{DEFERRED_FILE} lists {listed}"
                                 if args.apply else "   (dry run: nothing written)"))
    if args.apply and args.commit and commit_screen(written, screened, deferred):
        print(f"committed {screened} screen(s)")
    return EXIT_RATE_LIMITED if rate_limited else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
