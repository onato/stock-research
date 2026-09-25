#!/usr/bin/env python3
"""Emit a `claude --agents` override that moves pinned agents to another model.

    python3 scripts/agent_models.py [--dir .claude/agents] --from fable --to opus

When the weekly Fable window is spent (2026-09-25: seven_day_overage_included
at 101% while the account's own seven_day sat at 82%), every Fable-pinned
agent fails with HTTP 429 while every other model still works. lib.sh passes
this JSON to `claude --agents` so the batch keeps going on Opus.

A CLI-defined agent outranks .claude/agents/ of the same name, so the
override carries the whole definition -- description, tools and prompt --
not just the model; a partial one would replace the agent with an empty one.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

DEFAULT_DIR = pathlib.Path(__file__).resolve().parents[1] / ".claude" / "agents"


def parse(path: pathlib.Path) -> dict[str, Any]:
    """Frontmatter keys plus the body as `prompt`; `tools` as a list."""
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"{path}: no frontmatter")
    _, front, body = text.split("---", 2)
    out: dict[str, Any] = {}
    for line in front.strip().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    if "tools" in out:
        out["tools"] = [t.strip() for t in out["tools"].split(",") if t.strip()]
    out["prompt"] = body.strip()
    return out


def override(agents_dir: pathlib.Path, from_model: str, to_model: str) -> dict[str, Any]:
    """{name: definition} for every agent pinned to `from_model`, moved to `to_model`."""
    out: dict[str, Any] = {}
    for path in sorted(agents_dir.glob("*.md")):
        a = parse(path)
        if a.get("model") != from_model:
            continue
        d = {k: a[k] for k in ("description", "prompt", "tools") if k in a}
        d["model"] = to_model
        out[a.get("name") or path.stem] = d
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--dir", type=pathlib.Path, default=DEFAULT_DIR)
    ap.add_argument("--from", dest="from_model", required=True)
    ap.add_argument("--to", dest="to_model", required=True)
    args = ap.parse_args()
    json.dump(override(args.dir, args.from_model, args.to_model), sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
