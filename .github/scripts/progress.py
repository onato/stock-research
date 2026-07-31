#!/usr/bin/env python3
"""Render `claude --output-format stream-json` as readable progress lines.

The research-stock skill runs for ~40 minutes with no output under plain
`-p`, which is indistinguishable from a hang. This reads the JSON event
stream on stdin and prints one short line per meaningful event, so the run
is observable while it happens.

Deliberately a plain parser, not a model: stream-json is structured, so
every field below is a dict lookup. Reserve models for ambiguous input.

Full fidelity is preserved in the transcript written by `tee` upstream;
this only summarizes.
"""

import json
import sys
import time

# Tool-name -> how to describe the call. Each entry pulls the most
# informative field out of that tool's input dict.
def describe(tool, inp):
    if not isinstance(inp, dict):
        return tool

    if tool == "Bash":
        cmd = (inp.get("command") or "").strip().replace("\n", " ")
        desc = inp.get("description") or ""
        # Prefer the model's own one-line description when it wrote one.
        return f"$ {cmd[:100]}" if not desc else f"$ {desc[:80]}"
    if tool in ("Read", "Edit", "Write", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        return f"{tool} {shorten_path(path)}"
    if tool in ("Glob", "Grep"):
        return f"{tool} {inp.get('pattern', '')!r}"
    if tool == "WebFetch":
        return f"WebFetch {shorten_url(inp.get('url', ''))}"
    if tool == "WebSearch":
        return f"WebSearch {inp.get('query', '')[:70]!r}"
    if tool == "Task":
        return f"Task[{inp.get('subagent_type', '?')}] {inp.get('description', '')[:60]}"
    if tool == "Skill":
        return f"Skill /{inp.get('skill', '?')}"
    return tool


def shorten_path(p):
    """Trim absolute repo paths down to something readable."""
    if not p:
        return ""
    marker = "/Research/"
    if marker in p:
        p = p.split(marker, 1)[1]
    return p if len(p) <= 60 else "..." + p[-57:]


def shorten_url(u):
    return u if len(u) <= 60 else u[:57] + "..."


def main():
    # --tools-only drops the model's prose entirely, leaving just the tool
    # trace. Useful once you trust the run and only want to see movement.
    quiet_prose = "--tools-only" in sys.argv

    start = time.time()
    tools = 0
    model = None

    def stamp():
        el = int(time.time() - start)
        return f"[{el // 60:2d}:{el % 60:02d}]"

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            # Not JSON (a stray warning, say) -- pass it through rather
            # than swallowing something that might explain a failure.
            print(line, flush=True)
            continue

        kind = ev.get("type")

        if kind == "system" and ev.get("subtype") == "init":
            model = ev.get("model", "?")
            print(f"{stamp()} session started on {model}", flush=True)

        elif kind == "assistant":
            for block in ev.get("message", {}).get("content", []) or []:
                btype = block.get("type")
                if btype == "text":
                    text = (block.get("text") or "").strip()
                    # Narration between tool calls is the useful signal; the
                    # model's long-form prose duplicates the dashboard and
                    # the JSON reports, so keep only a short lead line.
                    if text and not quiet_prose:
                        first = text.split("\n", 1)[0].strip()
                        if first and len(first) > 3:
                            print(f"{stamp()} {first[:100]}", flush=True)
                elif btype == "tool_use":
                    tools += 1
                    print(
                        f"{stamp()}   -> {describe(block.get('name', '?'), block.get('input'))}",
                        flush=True,
                    )

        elif kind == "user":
            # Tool results come back as user-role messages. Surface only
            # errors -- successful results are far too voluminous to print.
            for block in ev.get("message", {}).get("content", []) or []:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(
                            c.get("text", "") for c in content if isinstance(c, dict)
                        )
                    msg = str(content or "").strip().replace("\n", " ")
                    print(f"{stamp()}   !! {msg[:110]}", flush=True)

        elif kind == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            if info.get("status") != "allowed":
                print(
                    f"{stamp()} RATE LIMIT: {info.get('status')} "
                    f"({info.get('rateLimitType')})",
                    flush=True,
                )

        elif kind == "result":
            cost = ev.get("total_cost_usd")
            turns = ev.get("num_turns")
            bits = [f"{tools} tool calls"]
            if turns:
                bits.append(f"{turns} turns")
            if cost is not None:
                bits.append(f"${cost:.2f}")
            status = "ERROR" if ev.get("is_error") else "done"
            print(f"{stamp()} {status} -- {', '.join(bits)}", flush=True)
            if ev.get("is_error"):
                res = str(ev.get("result") or "").strip().replace("\n", " ")
                if res:
                    print(f"{stamp()} {res[:200]}", flush=True)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
