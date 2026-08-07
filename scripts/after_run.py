#!/usr/bin/env python3
"""Post-run digest: what did this batch cost, was it good, what should change.

`make run` produces three separate signals -- cost transcripts, eval
scorecards, and the improvements log -- and reading them individually is
what stopped happening between sessions. This joins them into one page and,
crucially, ends with a ranked list of what to fix next.

Read-only. Suggests; never edits prompts or scripts.

Usage:
  after_run.py                 # digest the tickers in the last joblog
  after_run.py WISE.L PYPL     # specific tickers
"""

import collections
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SCORES = REPO / "state" / "scores"
JOBLOG = REPO / "state" / "joblog.tsv"

# What a ticker looks like: uppercase alphanumerics with an optional
# exchange suffix ("DUOL", "WISE.L", "1211.HK"). Anything else in the
# command's final position -- a path, a flag like "--fast" -- is not one.
TICKER_RE = re.compile(r"[A-Z0-9]+(?:\.[A-Z0-9]+)?")


def last_batch() -> list[str]:
    """Tickers from the most recent run_loop joblog.

    Falls back to the most recently written transcripts, so the digest is
    useful mid-run or after a `run_local.sh` invocation (which writes no
    joblog at all).
    """
    out = []
    if JOBLOG.exists():
        for line in JOBLOG.read_text(errors="replace").splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 2 or not parts[-1].strip():
                continue
            # The joblog's last column is the whole command
            # ("/path/to/research_one.sh WISE.L"), not just the ticker.
            token = parts[-1].strip().split()[-1]
            if TICKER_RE.fullmatch(token):
                out.append(token)
    if out:
        return out

    logs = REPO / "state" / "logs"
    if logs.is_dir():
        recent = sorted(logs.glob("*.log"), key=lambda p: p.stat().st_mtime,
                        reverse=True)[:6]
        out = [p.stem for p in recent]
    return out


def latest_scorecard(ticker: str) -> dict[str, Any] | None:
    # Newest by mtime, not by name: a re-run rewrites whichever file it
    # rewrites, and the freshest write is the card that reflects it.
    cards = sorted(SCORES.glob(f"{ticker}_*.json"),
                   key=lambda p: p.stat().st_mtime)
    if not cards:
        return None
    try:
        return json.loads(cards[-1].read_text())
    except Exception:
        return None


def run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              cwd=REPO, timeout=120, check=False).stdout
    except Exception as e:
        return f"(failed: {e})"


def main() -> int:
    tickers = sys.argv[1:] or last_batch()
    if not tickers:
        print("no tickers -- pass them explicitly or run the pipeline first")
        return 1

    print("=" * 66)
    print(f" POST-RUN DIGEST  ({len(tickers)} ticker(s): {', '.join(tickers)})")
    print("=" * 66)

    # ---- cost ------------------------------------------------------------
    print("\n## Cost\n")
    baseline = REPO / "state" / "cost_baseline.json"
    out = run(["python3", str(SCRIPTS / "cost_report.py"),
               *(["--compare", str(baseline)] if baseline.exists() else [])])
    for line in out.splitlines():
        if line.strip():
            print("  " + line)

    # ---- quality ---------------------------------------------------------
    print("\n## Quality (tier-1 evals)\n")
    problems: collections.Counter[str] = collections.Counter()
    per_ticker: dict[str, list[dict[str, Any]]] = {}
    for t in tickers:
        card = latest_scorecard(t)
        if not card:
            print(f"  {t:10s} no scorecard")
            continue
        # Scorecards are agent-written JSON: a check missing status/id/
        # detail degrades to an unknown-status warn with placeholders,
        # the same way malformed JSON degrades to "no scorecard".
        checks = [{"status": c.get("status", "warn"),
                   "id": c.get("id", "?"),
                   "detail": c.get("detail", "")}
                  for c in card.get("checks", [])]
        bad = [c for c in checks if c["status"] in ("warn", "fail")]
        per_ticker[t] = bad
        for c in bad:
            problems[c["id"]] += 1
        n_fail = sum(1 for c in bad if c["status"] == "fail")
        flag = "FAIL" if n_fail else ("warn" if bad else "clean")
        print(f"  {t:10s} {flag:5s} {len(bad):2d} issue(s)"
              f"   agents_sha={card.get('agents_sha','?')[:12]}")
        for c in bad[:4]:
            print(f"             {c['status']:4s} {c['id']:22s} {c['detail'][:56]}")

    # ---- what to fix -----------------------------------------------------
    print("\n## Suggested next actions\n")
    actions = []

    # Eval failures outrank everything: they mean the output is wrong.
    fails = [(t, c) for t, cs in per_ticker.items()
             for c in cs if c["status"] == "fail"]
    for t, c in fails[:5]:
        actions.append((0, f"FIX {t}: {c['id']} — {c['detail'][:70]}"))

    # A check warning on several tickers at once is systemic, not a quirk.
    for cid, n in problems.most_common():
        if n >= max(2, len(tickers) // 2):
            actions.append((1, (f"{cid} warns on {n}/{len(tickers)} tickers "
                                "— likely systemic, not per-ticker")))

    # Extractor gaps: the improvements log is the backlog of missing patterns.
    gaps = run(["python3", str(SCRIPTS / "log_gap.py"), "--report"])
    metric_lines = []
    grab = False
    for line in gaps.splitlines():
        if line.strip().startswith("by metric"):
            grab = True
            continue
        if grab:
            if not line.strip():
                break
            metric_lines.append(line.strip())
    if metric_lines:
        actions.append((2, (f"build_facts.py is missing {len(metric_lines)} "
                            "pattern(s) — see `make gaps`")))

    if not actions:
        print("  Nothing flagged. Clean batch.")
    else:
        for _, a in sorted(actions):
            print(f"  - {a}")

    if metric_lines:
        print("\n  Missing extractor patterns (most-wanted first):")
        for m in metric_lines[:8]:
            print(f"    {m}")

    print("\n" + "=" * 66)
    print("  Detail:  make cost | make evals-all | make gaps")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
