#!/usr/bin/env python3
"""Slowly fill in the company name and business description we do not have.

877 of the 1,784 queued tickers are a bare symbol -- no name, no sector, no
summary. `screen_ethics.py` marks them `status: unchecked`, and an unchecked
ticker is exactly the one that can waste a whole research run on a company
that was never going to pass the ethical screen.

BUILT TO CRAWL, NOT TO FINISH
-----------------------------
Yahoo rate-limited this machine into a multi-hour 429 cooldown during
development, from far less traffic than 877 lookups. So the pacing is the
feature, not an afterthought:

    * DELAY_SECONDS between every request, jittered so the pattern is not
      a metronome
    * an escalating backoff on 429 that starts at five minutes
    * DEFAULT_LIMIT requests per invocation, so a cron can trickle it out
    * resumable state, because a week-long job gets interrupted

Run it nightly for a week and it finishes. Run it flat out and it gets this
machine blocked, which is worse than not running it at all.

Source is stockanalysis.com, which unlike Yahoo answers today and covers every
market in the queue (US, HK, T, AX, NZ, L, TO, NS, PA, DE, SI, AS, BR, MI, ST,
CO). Its profile text is a real business description -- "Japan Tobacco Inc., a
tobacco company, engages in the manufacture and sale of tobacco products" --
which is far better classifier input than a name alone.

Writes only `name`, `sector` and `business_summary`, and never overwrites a
value that is already there: a curated name from the research pipeline beats
a scraped one.

Usage:
    backfill_profiles.py --dry-run            # what it would fetch
    backfill_profiles.py --limit 100          # one night's batch
    backfill_profiles.py --status             # progress so far
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_mod
import json
import pathlib
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "state" / "profile_backfill.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Pacing. These numbers exist because Yahoo 429'd this machine for hours at a
# far lower rate; stockanalysis is friendlier but is not to be hammered either.
DELAY_SECONDS = 6.0          # between successful requests
JITTER_SECONDS = 3.0         # added randomly, so it is not a metronome
FIRST_BACKOFF = 300.0        # a 429 means stop for five minutes, minimum
MAX_BACKOFF = 3600.0         # ...but never sleep more than an hour in one go
MAX_CONSECUTIVE_429 = 4      # after this many, the host wants us gone: stop
DEFAULT_LIMIT = 150          # per invocation; ~6 nights for 877

# stockanalysis.com uses a different path per market.
MARKETS: dict[str, str] = {
    "HK": "hkg", "T": "tyo", "AX": "asx", "NZ": "nzx", "L": "lon",
    "TO": "tsx", "NS": "nse", "PA": "epa", "DE": "etr", "SI": "sgx",
    "AS": "ams", "BR": "ebr", "MI": "bit", "ST": "sto", "CO": "cph",
}

# A failure that will repeat no matter how long we wait. Anything else (429,
# timeout, connection reset) says nothing about the ticker and is retried.
PERMANENT = {"no-url", "http-404", "no-profile"}


def profile_url(ticker: str) -> str | None:
    """The stockanalysis.com profile page for a ticker, or None if unmapped.

    Returning None for an unknown suffix is deliberate: guessing a path would
    bank a 404 as "this company has no profile", which is a different and
    wrong conclusion.
    """
    if "." not in ticker:
        return f"https://stockanalysis.com/stocks/{ticker}/company/"
    base, suffix = ticker.rsplit(".", 1)
    market = MARKETS.get(suffix.upper())
    if not market:
        return None
    return f"https://stockanalysis.com/quote/{market}/{base}/company/"


def backoff_seconds(consecutive: int) -> float:
    """Escalating, bounded wait after a 429."""
    return min(FIRST_BACKOFF * (2 ** (consecutive - 1)), MAX_BACKOFF)


def parse_profile(page: str) -> dict[str, str]:
    """Pull the business description out of a profile page.

    The description sits between the Description heading and the next section;
    anchoring on that is what keeps site navigation and the postal address out
    of the text the classifier will read.
    """
    # Real markup is `<h1 ...>Company Description</h1> <div ...><p>...</p>...`
    # -- there is no <h2>Description</h2>. Anchor on the h1 and stop at the
    # next heading or the close of the description div, which is what keeps
    # the Contact Details address and the nav out of the text.
    m = re.search(
        r"Company\s+Description\s*</h1>(.*?)(?=<h2|</section|<footer|\Z)",
        page, re.DOTALL | re.IGNORECASE)
    if not m:
        return {}
    paras: list[str] = []
    for raw in re.findall(r"<p[^>]*>(.*?)</p>", m.group(1), re.DOTALL):
        text = html_mod.unescape(re.sub(r"<[^>]+>", " ", raw))
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 60 and "Log In" not in text:
            paras.append(text)
    if not paras:
        return {}
    out = {"business_summary": " ".join(paras)[:2000]}

    # The h1 is the literal string "Company Description" on the live site, so
    # the name comes from <title>: "Ambev (ABEV) Company Profile & Description".
    for pat in (r"<title>([^<]{3,160})</title>",
                r"<h1[^>]*>([^<]{3,120})</h1>"):
        mt = re.search(pat, page, re.IGNORECASE)
        if not mt:
            continue
        n = html_mod.unescape(mt.group(1)).strip()
        n = re.sub(r"\s*Company\s+Profile.*$", "", n, flags=re.IGNORECASE).strip()
        n = re.sub(r"\s*Company\s+Description\s*$", "", n, flags=re.IGNORECASE).strip()
        n = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip()
        if n and n.lower() not in ("company description", "company profile"):
            out["name"] = n
            break
    return out


def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return str(r.read().decode("utf-8", "replace"))


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def load_state(path: pathlib.Path = STATE_PATH) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {"done": {}, "failed": {}}
    try:
        d = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"done": {}, "failed": {}}
    return {"done": d.get("done", {}), "failed": d.get("failed", {})}


def save_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def pending(tickers: list[str], state: dict[str, dict[str, str]]) -> list[str]:
    """What is still worth fetching.

    Done is done. A permanent failure (404, unmapped market) is not retried --
    it would burn the nightly budget on the same dead symbols every run. A
    transient one is retried, because a 429 says nothing about the ticker.
    """
    done = state.get("done", {})
    failed = state.get("failed", {})
    return [t for t in tickers
            if t not in done and failed.get(t) not in PERMANENT]


def merge_info(path: pathlib.Path, profile: dict[str, str]) -> None:
    """Fill gaps in info.json; never overwrite what is already there.

    A name written by the research pipeline or by hand carries context a
    scrape does not ("RTO Limited (formerly Blackwell Global Holdings)"), so
    an existing value always wins.
    """
    d: dict[str, Any] = {}
    if path.exists():
        try:
            d = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            d = {}
    wrote = False
    for key in ("name", "sector", "business_summary"):
        if profile.get(key) and not d.get(key):
            d[key] = profile[key]
            wrote = True
    if not wrote:
        return
    d["profile_source"] = "stockanalysis.com"
    d["profile_fetched_at"] = dt.date.today().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2) + "\n")


# --------------------------------------------------------------------------
# Commit
# --------------------------------------------------------------------------
def _git(*args: str, root: pathlib.Path = ROOT) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=False)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def commit_profiles(paths: list[str], filled: int,
                    root: pathlib.Path = ROOT) -> bool:
    """Commit the info.json files this run wrote, and nothing else.

    Run nightly and unattended, uncommitted output is a known failure mode in
    this repo: it piles up, gets swept into an unrelated ticker's commit by a
    `git add -A`, or is lost. So the paths are staged explicitly -- never -A,
    which would capture a concurrent research run's half-finished work.

    The resume state travels with them: a commit whose state file said the
    tickers were still pending would redo them all next night.
    """
    if not paths or filled <= 0:
        return False
    rc, _ = _git("add", *paths, "state/profile_backfill.json", root=root)
    if rc != 0:
        return False
    rc, _ = _git("diff", "--cached", "--quiet", root=root)
    if rc == 0:          # nothing actually staged
        return False
    msg = (f"chore: backfill {filled} company profile(s)\n\n"
           "Names and business summaries from stockanalysis.com, so the "
           "ethical screen can read tickers that were a bare symbol.\n"
           "Automated; no model, no valuation change.")
    rc, out = _git("commit", "-q", "-m", msg, root=root)
    if rc != 0:
        print(f"commit failed: {out}", file=sys.stderr)
        return False
    return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def unnamed_tickers(root: pathlib.Path = ROOT) -> list[str]:
    """Queued tickers with no local name -- the ones worth filling in."""
    import screen_ethics as se

    out = []
    for t in se.queued_tickers(root):
        _sector, name, _text, _ev = se.gather_text(t, root)
        if not name:
            out.append(t)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"tickers to fetch this run (default {DEFAULT_LIMIT})")
    p.add_argument("--dry-run", action="store_true", help="list, fetch nothing")
    p.add_argument("--status", action="store_true", help="progress so far")
    p.add_argument("--delay", type=float, default=DELAY_SECONDS,
                   help=f"seconds between requests (default {DELAY_SECONDS})")
    p.add_argument("--commit", action="store_true",
                   help="commit the info.json files written (for unattended runs)")
    args = p.parse_args(argv)

    state = load_state()
    todo = pending(unnamed_tickers(), state)

    if args.status:
        done, failed = len(state["done"]), len(state["failed"])
        print(f"filled {done}   failed {failed}   remaining {len(todo)}")
        if failed:
            from collections import Counter
            for reason, k in Counter(state["failed"].values()).most_common():
                print(f"  {reason:<14} {k}")
        if todo:
            nights = -(-len(todo) // max(args.limit, 1))
            print(f"\n~{nights} more run(s) of {args.limit} to finish")
        return 0

    batch = todo[: args.limit]
    if args.dry_run:
        for t in batch:
            print(f"{t:<12} {profile_url(t) or '(no url -- unmapped market)'}")
        print(f"\n{len(batch)} of {len(todo)} remaining "
              f"(~{args.delay + JITTER_SECONDS / 2:.0f}s each, "
              f"~{len(batch) * (args.delay + JITTER_SECONDS / 2) / 60:.0f} min)")
        return 0

    filled = failures = 0
    consecutive_429 = 0
    written: list[str] = []
    for i, t in enumerate(batch, 1):
        url = profile_url(t)
        if not url:
            state["failed"][t] = "no-url"
            failures += 1
            continue
        try:
            page = fetch(url)
            profile = parse_profile(page)
            if not profile:
                state["failed"][t] = "no-profile"
                failures += 1
            else:
                merge_info(ROOT / "research" / t / "info.json", profile)
                written.append(f"research/{t}/info.json")
                state["done"][t] = dt.date.today().isoformat()
                state["failed"].pop(t, None)
                filled += 1
                print(f"[{i}/{len(batch)}] {t:<12} {profile.get('name', '?')[:52]}")
            consecutive_429 = 0
        except urllib.error.HTTPError as e:
            if e.code == 429:
                consecutive_429 += 1
                state["failed"][t] = "http-429"
                if consecutive_429 >= MAX_CONSECUTIVE_429:
                    print(f"\n{consecutive_429} rate-limits in a row -- stopping. "
                          "Rerun later; progress is saved.", file=sys.stderr)
                    break
                wait = backoff_seconds(consecutive_429)
                print(f"  rate-limited, sleeping {wait / 60:.0f} min", file=sys.stderr)
                save_state(STATE_PATH, state)
                time.sleep(wait)
                continue
            state["failed"][t] = f"http-{e.code}"
            failures += 1
        except Exception as e:
            state["failed"][t] = type(e).__name__
            failures += 1

        save_state(STATE_PATH, state)
        if i < len(batch):
            time.sleep(args.delay + random.uniform(0, JITTER_SECONDS))

    save_state(STATE_PATH, state)
    left = len(pending(unnamed_tickers(), state))
    print(f"\nfilled {filled}   failed {failures}   remaining {left}")
    if args.commit and commit_profiles(written, filled):
        print(f"committed {filled} profile(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
