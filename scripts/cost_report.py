#!/usr/bin/env python3
"""Measure what a research run cost, and where.

Optimisation needs a before/after number. This reads the stream-json
transcripts in state/logs/ and reports per-ticker cost, plus a
per-subagent breakdown -- which is how the financial-parser bottleneck
(183 turns, 18.2M cache-read tokens, ~60% of AFC.NZ's cost) was found.

The `result` event's usage covers only the MAIN thread, so total_cost_usd
understates nothing but explains little. Subagent traffic is attributed
via parent_tool_use_id on each assistant message.

Usage:
  cost_report.py                     # every transcript
  cost_report.py AFC.NZ              # one ticker, with subagent breakdown
  cost_report.py --baseline out.json # save current numbers as a baseline
  cost_report.py --compare out.json  # diff against a saved baseline
"""

import collections
import json
import pathlib
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
LOGS = REPO / "state" / "logs"

# $/token. Cache reads bill at 0.1x input; 1h-TTL writes at 2x.
RATES = {
    "claude-fable-5":            (10.0e-6, 50.0e-6),
    "claude-opus-5":             (5.0e-6,  25.0e-6),
    "claude-sonnet-5":           (3.0e-6,  15.0e-6),
    "claude-haiku-4-5-20251001": (1.0e-6,   5.0e-6),
}


def rate(model: str | None) -> tuple[float, float]:
    for k, v in RATES.items():
        if model and model.startswith(k[:18]):
            return v
    return (3.0e-6, 15.0e-6)


def cost_of(model: str | None, fresh: int, write: int, read: int, out: int) -> float:
    rin, rout = rate(model)
    return fresh * rin + write * rin * 2.0 + read * rin * 0.1 + out * rout


def analyse(path: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (summary, per-subagent rows) for one transcript."""
    agents: collections.defaultdict[str, dict[str, Any]] = collections.defaultdict(
        lambda: {"msgs": 0, "fresh": 0, "write": 0, "read": 0, "out": 0,
                 "model": None, "tools": collections.Counter(), "label": ""})
    reported = None
    turns = 0
    # Claude Code writes one line per content block, each repeating the
    # whole message's usage; count a message id once.
    seen_ids: set[str] = set()

    with open(path, errors="replace") as fh:
        events = [line for raw in fh if (line := raw.strip()).startswith("{")]
    for line in events:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue

        if ev.get("type") == "result":
            reported = ev.get("total_cost_usd")
            turns = ev.get("num_turns") or 0
            continue
        if ev.get("type") != "assistant":
            continue

        pid = ev.get("parent_tool_use_id") or "MAIN"
        msg = ev.get("message", {})
        mid = msg.get("id")
        if mid:
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
        u = msg.get("usage") or {}
        a = agents[pid]
        a["msgs"] += 1
        a["model"] = msg.get("model") or a["model"]
        a["fresh"] += u.get("input_tokens", 0)
        a["write"] += u.get("cache_creation_input_tokens", 0)
        a["read"] += u.get("cache_read_input_tokens", 0)
        a["out"] += u.get("output_tokens", 0)
        for b in msg.get("content", []) or []:
            if b.get("type") == "tool_use":
                a["tools"][b.get("name")] += 1
            elif b.get("type") == "text" and not a["label"]:
                t = (b.get("text") or "").strip()
                if t:
                    a["label"] = t.split("\n")[0][:58]

    rows = []
    for pid, a in agents.items():
        est = cost_of(a["model"], a["fresh"], a["write"], a["read"], a["out"])
        rows.append({"id": pid, "est": est, **a})
    rows.sort(key=lambda r: -r["est"])

    total_est = sum(r["est"] for r in rows)
    return {
        "ticker": path.stem,
        "reported": reported,
        "estimated": total_est,
        "turns": turns,
        "read": sum(r["read"] for r in rows),
        "out": sum(r["out"] for r in rows),
        "subagents": len(rows) - (1 if any(r["id"] == "MAIN" for r in rows) else 0),
    }, rows


def show_detail(summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    print(f"\n=== {summary['ticker']} ===")
    rep = summary["reported"]
    print(f"  reported ${rep:.2f}" if rep is not None else "  (incomplete run)", end="")
    print(f"   modelled ${summary['estimated']:.2f}"
          f"   {summary['subagents']} subagent(s)"
          f"   {summary['read']:,} cache-read tokens")
    print(f"\n  {'cost':>7s} {'msgs':>5s} {'cache_read':>12s}  model / top tools")
    for r in rows:
        tools = ", ".join(f"{k}x{v}" for k, v in r["tools"].most_common(3))
        who = "MAIN THREAD" if r["id"] == "MAIN" else (r["label"] or "subagent")
        print(f"  ${r['est']:6.2f} {r['msgs']:5d} {r['read']:12,d}  "
              f"{(r['model'] or '?').replace('claude-','')[:16]:16s} [{tools}]")
        print(f"          {who}")


def main() -> int:
    # Drop the value that follows --baseline/--compare so it is not
    # mistaken for a ticker name.
    argv, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--baseline", "--compare"):
            skip = True
            continue
        if not a.startswith("--"):
            argv.append(a)
    args = argv
    if not LOGS.is_dir():
        print("no transcripts yet -- run the screener first", file=sys.stderr)
        return 1

    paths = ([LOGS / f"{t}.log" for t in args] if args
             else sorted(LOGS.glob("*.log")))
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("no matching transcripts", file=sys.stderr)
        return 1

    results = []
    for p in paths:
        summary, rows = analyse(p)
        results.append(summary)
        if args or "--detail" in sys.argv:
            show_detail(summary, rows)

    print(f"\n{'ticker':11s} {'cost':>8s} {'turns':>6s} {'subagents':>10s} {'cache_read':>13s}")
    print("-" * 54)
    tot = 0.0
    for s in results:
        c = s["reported"] if s["reported"] is not None else s["estimated"]
        tot += c or 0
        print(f"{s['ticker']:11s} ${c or 0:7.2f} {s['turns']:6d} "
              f"{s['subagents']:10d} {s['read']:13,d}")
    print("-" * 54)
    n = len(results)
    print(f"{'TOTAL':11s} ${tot:7.2f}   mean ${tot/n:.2f}/ticker over {n}")

    # --baseline / --compare make optimisation measurable across changes.
    for flag in ("--baseline", "--compare"):
        if flag in sys.argv:
            i = sys.argv.index(flag)
            if i + 1 >= len(sys.argv):
                print(f"  {flag} needs a filename", file=sys.stderr)
                return 2
            fp = pathlib.Path(sys.argv[i + 1])
            # Same selection as the summary table: a reported $0.00 is a
            # real number, not a cue to fall back to the estimate.
            snap = {s["ticker"]: (s["reported"] if s["reported"] is not None
                                  else s["estimated"]) for s in results}
            if flag == "--baseline":
                fp.write_text(json.dumps(snap, indent=2))
                print(f"\n  baseline saved to {fp}")
            else:
                if not fp.exists():
                    print(f"  no baseline at {fp}", file=sys.stderr)
                    return 1
                base = json.loads(fp.read_text())
                print(f"\n  {'ticker':11s} {'before':>8s} {'after':>8s} {'change':>10s}")
                for t, after in snap.items():
                    before = base.get(t)
                    if before is None:
                        # Pad the change column so the table stays aligned.
                        print(f"  {t:11s} {'(new)':>8s} ${after:7.2f} {'--':>10s}")
                        continue
                    pct = (after - before) / before * 100 if before else 0
                    print(f"  {t:11s} ${before:7.2f} ${after:7.2f} {pct:+9.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
