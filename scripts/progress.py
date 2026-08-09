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
from typing import Any


# Tool-name -> how to describe the call. Each entry pulls the most
# informative field out of that tool's input dict.
def describe(tool: str, inp: Any) -> str:
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
    if tool in ("Task", "Agent"):
        # Both spellings launch a subagent. These are the calls that run for
        # many minutes, so naming which agent is working is the difference
        # between "still working" and knowing what it is waiting on.
        return (f"{tool}[{inp.get('subagent_type', '?')}] "
                f"{inp.get('description', '')[:60]}")
    if tool == "Skill":
        return f"Skill /{inp.get('skill', '?')}"
    return tool


def shorten_path(p: str) -> str:
    """Trim absolute repo paths down to something readable."""
    if not p:
        return ""
    marker = "/Research/"
    if marker in p:
        p = p.split(marker, 1)[1]
    return p if len(p) <= 60 else "..." + p[-57:]


def shorten_url(u: str) -> str:
    return u if len(u) <= 60 else u[:57] + "..."


def content_blocks(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """A message's content as a list of block dicts.

    stream-json emits plain-string content for simple messages; wrap it
    as a single text block rather than iterating it character-by-character.
    """
    content = ev.get("message", {}).get("content") or []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)]


def _flag_value(argv: list[str], name: str, default: str = "") -> str:
    """Value following `--name`, or `default` if the flag is absent."""
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError):
        return default


def main() -> int:
    # --tools-only drops the model's prose entirely, leaving just the tool
    # trace. Useful once you trust the run and only want to see movement.
    quiet_prose = "--tools-only" in sys.argv

    # --label tags every line. run_loop runs several tickers concurrently and
    # merges their streams; without a tag you cannot tell which one stalled.
    label = _flag_value(sys.argv, "--label")
    prefix = f"[{label}] " if label else ""

    # --heartbeat N prints a "still working" line when N seconds pass with no
    # event. The research run calls subagents that work for many minutes in
    # one tool call, and that silence is indistinguishable from a hang -- the
    # reason a batch run looked stuck.
    try:
        heartbeat = float(_flag_value(sys.argv, "--heartbeat", "0"))
    except ValueError:
        heartbeat = 0.0

    start = time.time()
    tools = 0
    model = None
    last_tool = ""
    last_event = time.monotonic()

    def stamp() -> str:
        el = int(time.time() - start)
        return f"[{el // 60:2d}:{el % 60:02d}]"

    def say(text: str) -> None:
        print(f"{prefix}{text}", flush=True)

    def beat() -> None:
        """Report the wait if this event followed a long silence."""
        nonlocal last_event
        now = time.monotonic()
        if heartbeat and now - last_event >= heartbeat:
            waited = int(now - last_event)
            detail = f", still on {last_tool}" if last_tool else ""
            say(f"{stamp()}   .. still working ({waited}s, "
                f"{tools} tool call{'' if tools == 1 else 's'}{detail})")
        last_event = now

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        beat()
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            # Not JSON (a stray warning, say) -- pass it through rather
            # than swallowing something that might explain a failure.
            say(line)
            continue

        kind = ev.get("type")

        if kind == "system" and ev.get("subtype") == "init":
            model = ev.get("model", "?")
            say(f"{stamp()} session started on {model}")

        elif kind == "assistant":
            for block in content_blocks(ev):
                btype = block.get("type")
                if btype == "text":
                    text = (block.get("text") or "").strip()
                    # Narration between tool calls is the useful signal; the
                    # model's long-form prose duplicates the dashboard and
                    # the JSON reports, so keep only a short lead line.
                    if text and not quiet_prose:
                        first = text.split("\n", 1)[0].strip()
                        if first and len(first) > 3:
                            say(f"{stamp()} {first[:100]}")
                elif btype == "tool_use":
                    tools += 1
                    what = describe(block.get("name", "?"), block.get("input"))
                    # Remembered so a heartbeat during a long call can say
                    # what it is waiting on, not just that it is waiting.
                    last_tool = what
                    say(f"{stamp()}   -> {what}")

        elif kind == "user":
            # Tool results come back as user-role messages. Surface only
            # errors -- successful results are far too voluminous to print.
            for block in content_blocks(ev):
                if block.get("type") == "tool_result" and block.get("is_error"):
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(
                            c.get("text", "") for c in content if isinstance(c, dict)
                        )
                    msg = str(content or "").strip().replace("\n", " ")
                    say(f"{stamp()}   !! {msg[:110]}")

        elif kind == "rate_limit_event":
            # No payload means nothing worth reporting -- skip rather than
            # printing a "RATE LIMIT: None (None)" line.
            info = ev.get("rate_limit_info") or {}
            if info and info.get("status") != "allowed":
                say(f"{stamp()} RATE LIMIT: {info.get('status')} "
                    f"({info.get('rateLimitType')})")

        elif kind == "result":
            cost = ev.get("total_cost_usd")
            turns = ev.get("num_turns")
            bits = [f"{tools} tool calls"]
            # 0 turns is a real value, not an absence -- match cost's
            # `is not None` rather than truthiness.
            if turns is not None:
                bits.append(f"{turns} turns")
            if cost is not None:
                bits.append(f"${cost:.2f}")
            status = "ERROR" if ev.get("is_error") else "done"
            say(f"{stamp()} {status} -- {', '.join(bits)}")
            if ev.get("is_error"):
                res = str(ev.get("result") or "").strip().replace("\n", " ")
                if res:
                    say(f"{stamp()} {res[:200]}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
