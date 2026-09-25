"""agent_models.py rewrites Fable-pinned agents for a `claude --agents` override.

When the weekly Fable window is spent, the batch keeps going with the same
agent definitions on Opus. A CLI-defined agent outranks .claude/agents/
(verified 2026-09-25: an --agents dcf-analyst replaced the project one), so
the override must carry the whole definition -- prompt and tools included --
or the fallback agent would run with no instructions.
"""

import json
import sys

import agent_models
import pytest

AGENT = """---
name: dcf-analyst
description: Judges the DCF drivers
tools: Read, Write, Bash, Glob
model: fable
---

You create DCF valuation models.

Second paragraph: with a colon.
"""

OTHER = """---
name: financial-parser
description: Reviews the worksheet
tools: Bash, Read
model: opus
---

Parse.
"""


@pytest.fixture
def agents_dir(tmp_path):
    (tmp_path / "dcf-analyst.md").write_text(AGENT)
    (tmp_path / "financial-parser.md").write_text(OTHER)
    return tmp_path


def test_parse_splits_frontmatter_and_body(agents_dir):
    a = agent_models.parse(agents_dir / "dcf-analyst.md")
    assert a["name"] == "dcf-analyst"
    assert a["model"] == "fable"
    assert a["tools"] == ["Read", "Write", "Bash", "Glob"]
    assert a["prompt"].startswith("You create DCF valuation models.")
    assert "Second paragraph: with a colon." in a["prompt"]


def test_override_swaps_only_the_pinned_model(agents_dir):
    out = agent_models.override(agents_dir, "fable", "opus")
    assert set(out) == {"dcf-analyst"}
    d = out["dcf-analyst"]
    assert d["model"] == "opus"
    assert d["description"] == "Judges the DCF drivers"
    assert d["tools"] == ["Read", "Write", "Bash", "Glob"]
    assert "You create DCF valuation models." in d["prompt"]


def test_no_pinned_agent_yields_an_empty_override(agents_dir):
    assert agent_models.override(agents_dir, "haiku", "opus") == {}


def test_cli_prints_json(agents_dir, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agent_models.py", "--dir", str(agents_dir),
                                      "--from", "fable", "--to", "opus"])
    assert agent_models.main() == 0
    assert json.loads(capsys.readouterr().out)["dcf-analyst"]["model"] == "opus"


def test_repo_dcf_analyst_is_the_fable_agent():
    # The fallback exists for this agent; if it moves off Fable the marker
    # is harmless, but the tiering docs in lib.sh would be stale.
    from pathlib import Path
    root = Path(__file__).parents[1] / ".claude" / "agents"
    assert "dcf-analyst" in agent_models.override(root, "fable", "opus")
