#!/usr/bin/env python3
"""Measure what a research run cost, and where.

Optimisation needs a before/after number. This reads the stream-json
transcripts in state/logs/ and reports per-ticker cost, plus a
per-subagent breakdown -- which is how the financial-parser bottleneck
(183 turns, 18.2M cache-read tokens, ~60% of AFC.NZ's cost) was found.

The `result` event's usage covers only the MAIN thread, so total_cost_usd
understates nothing but explains little. Subagent traffic is attributed
via parent_tool_use_id on each assistant message.

Subagent rows are named by the `subagent_type` of the Agent (or legacy
Task) call that spawned them. The first prose line a subagent emitted is
kept as a label, but it was useless as a key: across 40 logs it made the
per-stage picture unreadable, which is how the parser kept its reputation
as the bottleneck for a fortnight after the worksheet had fixed it.

Usage:
  cost_report.py                     # every transcript
  cost_report.py AFC.NZ              # one ticker, with subagent breakdown
  cost_report.py --baseline out.json # save current numbers as a baseline
  cost_report.py --compare out.json  # diff against a saved baseline
  cost_report.py --stage             # mean $/run per stage -> STAGES_OUT
  cost_report.py --since 2026-08-21  # only runs started on/after the date
"""

import collections
import datetime as dt
import json
import pathlib
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
LOGS = REPO / "state" / "logs"
STAGES_OUT = REPO / "state" / "cost_baseline_stages.json"
SPAWN_TOOLS = ("Agent", "Task")

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
    # tool_use id of each Agent/Task spawn -> its subagent_type, so the
    # subagent's messages (parent_tool_use_id == that id) get a stage name.
    spawned: dict[str, str] = {}
    reported = None
    turns = 0
    started: str | None = None
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

        ts = ev.get("timestamp")
        if started is None and isinstance(ts, str) and len(ts) >= 10:
            started = ts[:10]
        if ev.get("type") == "result":
            reported = ev.get("total_cost_usd")
            turns = ev.get("num_turns") or 0
            continue
        if ev.get("type") != "assistant":
            continue

        pid = ev.get("parent_tool_use_id") or "MAIN"
        msg = ev.get("message", {})
        # Claude Code writes one line per content block, all carrying the
        # same message id. The Agent block usually sits on a later line
        # than the text block that first claimed the id, so record spawns
        # before the dedup below or most stages read as "subagent".
        for b in msg.get("content", []) or []:
            if (b.get("type") == "tool_use" and b.get("name") in SPAWN_TOOLS
                    and b.get("id")):
                kind = (b.get("input") or {}).get("subagent_type")
                spawned[b["id"]] = kind or "subagent"
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
        stage = "MAIN" if pid == "MAIN" else spawned.get(pid, "subagent")
        rows.append({"id": pid, "est": est, "stage": stage, **a})
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
        "started": started,
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
        who = "MAIN THREAD" if r["id"] == "MAIN" else r["stage"]
        if r["id"] != "MAIN" and r["label"]:
            who += f"  -- {r['label']}"
        print(f"  ${r['est']:6.2f} {r['msgs']:5d} {r['read']:12,d}  "
              f"{(r['model'] or '?').replace('claude-','')[:16]:16s} [{tools}]")
        print(f"          {who}")


def stage_report(per_run_rows: list[list[dict[str, Any]]],
                 since: str | None) -> dict[str, Any]:
    """Mean modelled $ per run for each stage over a set of transcripts.

    A stage's `runs` counts transcripts in which it appeared at all, so a
    stage skipped by half the runs is not diluted by the runs that never
    spawned it; `share` is its slice of the modelled total.
    """
    total: collections.Counter[str] = collections.Counter()
    runs: collections.Counter[str] = collections.Counter()
    msgs: collections.Counter[str] = collections.Counter()
    read: collections.Counter[str] = collections.Counter()
    for rows in per_run_rows:
        seen: set[str] = set()
        for r in rows:
            st = r["stage"]
            total[st] += r["est"]
            msgs[st] += r["msgs"]
            read[st] += r["read"]
            seen.add(st)
        for st in seen:
            runs[st] += 1
    grand = sum(total.values()) or 1.0
    stages = {
        st: {"runs": runs[st], "total": round(total[st], 4),
             "per_run": round(total[st] / runs[st], 4),
             "share": round(total[st] / grand, 4),
             "msgs_per_run": round(msgs[st] / runs[st], 1),
             "cache_read_per_run": round(read[st] / runs[st])}
        for st in sorted(total, key=lambda k: -total[k])
    }
    n = len(per_run_rows)
    return {"since": since, "runs": n,
            "mean_per_run": round(grand / n, 4) if n else 0.0,
            "stages": stages}


def show_stages(snap: dict[str, Any]) -> None:
    print(f"\n  per-stage modelled cost over {snap['runs']} run(s)"
          + (f" since {snap['since']}" if snap["since"] else "")
          + f" -- mean ${snap['mean_per_run']:.2f}/run")
    print(f"  {'stage':22s} {'$/run':>7s} {'share':>6s} {'runs':>5s} "
          f"{'msgs/run':>9s} {'cache_read/run':>15s}")
    for st, v in snap["stages"].items():
        print(f"  {st:22s} ${v['per_run']:6.2f} {v['share']*100:5.1f}% {v['runs']:5d} "
              f"{v['msgs_per_run']:9.1f} {v['cache_read_per_run']:15,d}")


def main() -> int:
    # Drop the value that follows --baseline/--compare/--since so it is
    # not mistaken for a ticker name.
    argv, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--baseline", "--compare", "--since"):
            skip = True
            continue
        if not a.startswith("--"):
            argv.append(a)
    args = argv
    since: str | None = None
    if "--since" in sys.argv:
        i = sys.argv.index("--since")
        if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("--"):
            print("  --since needs a date (YYYY-MM-DD)", file=sys.stderr)
            return 2
        since = sys.argv[i + 1]
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
    all_rows: list[list[dict[str, Any]]] = []
    for p in paths:
        summary, rows = analyse(p)
        if since:
            # Transcripts predating the timestamp field fall back to mtime.
            started = summary["started"] or dt.date.fromtimestamp(
                p.stat().st_mtime).isoformat()
            if started < since:
                continue
        results.append(summary)
        all_rows.append(rows)
        if args or "--detail" in sys.argv:
            show_detail(summary, rows)
    if not results:
        print("no transcripts in range", file=sys.stderr)
        return 1

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

    if "--stage" in sys.argv:
        snap = stage_report(all_rows, since)
        show_stages(snap)
        STAGES_OUT.parent.mkdir(parents=True, exist_ok=True)
        STAGES_OUT.write_text(json.dumps(snap, indent=2))
        print(f"\n  stage snapshot saved to {STAGES_OUT}")

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
