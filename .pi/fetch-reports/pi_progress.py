#!/usr/bin/env python3
"""Turn pi's --mode json event stream into terse live progress lines.

Shows each tool call as it starts (what it searched, what it's downloading),
tool failures, and the model's final text. Reads stdin, writes stdout,
line-buffered so run.sh's timestamper stamps events in real time.
"""
import json
import sys


def brief(args: dict) -> str:
    for key in ("command", "query", "queries", "url", "path", "file_path", "pattern", "agent"):
        if key in args:
            v = args[key]
            v = " ".join(v) if isinstance(v, list) else str(v)
            v = " ".join(v.split())
            return v[:160] + ("…" if len(v) > 160 else "")
    return json.dumps(args)[:120]


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            print(line, flush=True)          # pass through non-JSON noise
            continue
        t = ev.get("type")
        if t == "tool_execution_start":
            print(f"  → {ev.get('toolName')}: {brief(ev.get('args') or {})}", flush=True)
        elif t == "tool_execution_end" and ev.get("isError"):
            r = ev.get("result")
            r = json.dumps(r) if not isinstance(r, str) else r
            print(f"  ✗ {ev.get('toolName')} failed: {' '.join(str(r).split())[:160]}", flush=True)
        elif t == "message_end":
            msg = ev.get("message") or {}
            if msg.get("role") == "assistant":
                text = " ".join(
                    c.get("text", "") for c in msg.get("content", [])
                    if isinstance(c, dict) and c.get("type") == "text"
                ).strip()
                if text:
                    print(f"  model: {' '.join(text.split())[:300]}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
