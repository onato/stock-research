"""Tests for progress.py: stream-json events -> one-line progress trace.

The contract that matters: every meaningful event yields exactly one short
line, errors surface while successes stay quiet, and non-JSON input passes
through rather than being swallowed (it might explain a failure).
"""

import io
import json
import sys

import progress


def render(monkeypatch, capsys, events, argv=()):
    """Feed events (dicts or raw strings) through main(); return stdout."""
    text = "\n".join(
        e if isinstance(e, str) else json.dumps(e) for e in events)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text + "\n"))
    monkeypatch.setattr(sys, "argv", ["progress.py", *argv])
    assert progress.main() == 0
    return capsys.readouterr().out


def assistant(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


def tool_use(name, inp):
    return {"type": "tool_use", "name": name, "input": inp}


class TestDescribe:
    def test_bash_prefers_models_own_description(self):
        assert progress.describe(
            "Bash", {"command": "ls -la", "description": "List files"},
        ) == "$ List files"

    def test_bash_falls_back_to_flattened_command(self):
        # Multi-line commands must stay on one progress line.
        assert progress.describe(
            "Bash", {"command": "ls\n-la"}) == "$ ls -la"

    def test_file_tools_show_shortened_path(self):
        got = progress.describe(
            "Read", {"file_path": "/Users/x/Stocks/Research/scripts/schema.py"})
        assert got == "Read scripts/schema.py"

    def test_notebook_edit_uses_notebook_path(self):
        got = progress.describe("NotebookEdit", {"notebook_path": "nb.ipynb"})
        assert got == "NotebookEdit nb.ipynb"

    def test_glob_and_grep_show_pattern(self):
        assert progress.describe("Grep", {"pattern": "revenue"}) == "Grep 'revenue'"

    def test_webfetch_shows_shortened_url(self):
        assert progress.describe(
            "WebFetch", {"url": "https://x.com/a"}) == "WebFetch https://x.com/a"

    def test_websearch_shows_query(self):
        assert progress.describe(
            "WebSearch", {"query": "DUOL earnings"}) == "WebSearch 'DUOL earnings'"

    def test_task_shows_subagent_and_description(self):
        got = progress.describe(
            "Task", {"subagent_type": "dcf-analyst", "description": "Build DCF"})
        assert got == "Task[dcf-analyst] Build DCF"

    def test_skill_is_slash_prefixed(self):
        assert progress.describe(
            "Skill", {"skill": "research-stock"}) == "Skill /research-stock"

    def test_unknown_tool_is_just_its_name(self):
        assert progress.describe("Artifact", {"anything": 1}) == "Artifact"

    def test_non_dict_input_is_just_the_tool_name(self):
        # Malformed input must never crash the progress stream.
        assert progress.describe("Bash", None) == "Bash"
        assert progress.describe("Bash", "ls") == "Bash"


class TestShorteners:
    def test_repo_paths_are_relativized(self):
        assert progress.shorten_path(
            "/Users/x/Stocks/Research/research/DUOL/Reports/a.csv",
        ) == "research/DUOL/Reports/a.csv"

    def test_long_paths_keep_the_tail(self):
        p = "/other/" + "a" * 70 + "/file.txt"
        got = progress.shorten_path(p)
        assert got.startswith("...")
        assert got.endswith("/file.txt")
        assert len(got) == 60

    def test_empty_path_is_empty(self):
        assert progress.shorten_path("") == ""

    def test_short_url_untouched_long_url_truncated(self):
        assert progress.shorten_url("https://x.com/a") == "https://x.com/a"
        long_url = "https://example.com/" + "b" * 60
        got = progress.shorten_url(long_url)
        assert got.endswith("...")
        assert len(got) == 60


class TestMain:
    def test_non_json_passes_through(self, monkeypatch, capsys):
        # A stray warning on stdin might explain a failure; never swallow it.
        out = render(monkeypatch, capsys, ["some stderr-ish warning"])
        assert "some stderr-ish warning" in out

    def test_blank_lines_produce_no_output(self, monkeypatch, capsys):
        assert render(monkeypatch, capsys, ["", "   "]) == ""

    def test_init_announces_model(self, monkeypatch, capsys):
        out = render(monkeypatch, capsys, [
            {"type": "system", "subtype": "init", "model": "claude-fable-5"}])
        assert "session started on claude-fable-5" in out

    def test_assistant_prose_keeps_only_first_line(self, monkeypatch, capsys):
        out = render(monkeypatch, capsys, [
            assistant({"type": "text",
                       "text": "Reading the annual report\nlong paragraph two"})])
        assert "Reading the annual report" in out
        assert "long paragraph two" not in out

    def test_tiny_prose_fragments_are_dropped(self, monkeypatch, capsys):
        # len(first) > 3 gate: "ok." style fragments are noise.
        assert render(monkeypatch, capsys,
                      [assistant({"type": "text", "text": "ok."})]) == ""

    def test_tools_only_flag_drops_prose_keeps_tools(self, monkeypatch, capsys):
        out = render(monkeypatch, capsys, [
            assistant({"type": "text", "text": "Narration to hide"},
                      tool_use("Grep", {"pattern": "npat"}))],
            argv=["--tools-only"])
        assert "Narration to hide" not in out
        assert "Grep 'npat'" in out

    def test_tool_error_results_surface_successes_stay_quiet(
            self, monkeypatch, capsys):
        """Only is_error tool results print; success output is far too
        voluminous. List-form content is joined into one line."""
        out = render(monkeypatch, capsys, [
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "is_error": True,
                 "content": [{"type": "text", "text": "file not"},
                             {"type": "text", "text": "found"}]}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result",
                 "content": "8000 lines of successful output"}]}},
        ])
        assert "!! file not found" in out
        assert "8000 lines" not in out

    def test_rate_limit_only_prints_when_not_allowed(self, monkeypatch, capsys):
        out = render(monkeypatch, capsys, [
            {"type": "rate_limit_event",
             "rate_limit_info": {"status": "allowed", "rateLimitType": "x"}},
            {"type": "rate_limit_event",
             "rate_limit_info": {"status": "rejected",
                                 "rateLimitType": "output_tokens"}},
        ])
        assert out.count("RATE LIMIT") == 1
        assert "RATE LIMIT: rejected (output_tokens)" in out

    def test_result_summarizes_tool_count_turns_and_cost(
            self, monkeypatch, capsys):
        out = render(monkeypatch, capsys, [
            assistant(tool_use("Read", {"file_path": "a"}),
                      tool_use("Read", {"file_path": "b"})),
            {"type": "result", "num_turns": 7, "total_cost_usd": 1.234},
        ])
        assert "done -- 2 tool calls, 7 turns, $1.23" in out

    def test_result_omits_missing_turns_and_cost(self, monkeypatch, capsys):
        out = render(monkeypatch, capsys, [{"type": "result"}])
        assert "done -- 0 tool calls" in out
        assert "turns" not in out
        assert "$" not in out

    def test_error_result_prints_status_and_message(self, monkeypatch, capsys):
        out = render(monkeypatch, capsys, [
            {"type": "result", "is_error": True,
             "result": "budget exceeded\nsecond line"}])
        assert "ERROR -- 0 tool calls" in out
        assert "budget exceeded second line" in out

    def test_zero_cost_still_prints_but_zero_turns_dropped(
            self, monkeypatch, capsys):
        # Documents the asymmetry: cost uses `is not None`, turns uses
        # truthiness, so a 0-turn result silently drops the turn count.
        out = render(monkeypatch, capsys, [
            {"type": "result", "num_turns": 0, "total_cost_usd": 0.0}])
        assert "$0.00" in out
        assert "turns" not in out
