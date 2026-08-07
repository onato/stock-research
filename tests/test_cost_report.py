"""Tests for cost_report.py: transcript cost modelling and attribution.

analyse() attributes token usage to subagents via parent_tool_use_id and
models dollar cost from per-model rates (cache reads bill at 0.1x input,
1h-TTL cache writes at 2x); main() renders the per-ticker table and the
--baseline/--compare snapshot diff. Every transcript here is a synthetic
stream-json file under tmp_path — never live state/logs/.
"""

import json
import sys

import cost_report
import pytest


def assistant(model="claude-sonnet-5-20250929", parent=None, usage=None,
              content=None):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"model": model, "usage": usage or {},
                        "content": content or []}}


def result(cost=1.23, turns=7):
    return {"type": "result", "total_cost_usd": cost, "num_turns": turns}


def write_log(path, events):
    lines = [e if isinstance(e, str) else json.dumps(e) for e in events]
    path.write_text("\n".join(lines) + "\n")
    return path


class TestRate:
    def test_known_models_match_by_prefix(self):
        # Dated snapshot ids must resolve to the same rate as the family.
        assert cost_report.rate("claude-fable-5") == (10.0e-6, 50.0e-6)
        assert cost_report.rate("claude-opus-5-20250101") == (5.0e-6, 25.0e-6)
        assert cost_report.rate("claude-sonnet-5-20250929") == (3.0e-6, 15.0e-6)
        assert cost_report.rate("claude-haiku-4-5-20251001") == (1.0e-6, 5.0e-6)

    def test_unknown_or_missing_model_uses_sonnet_default(self):
        assert cost_report.rate("gpt-oss-120b") == (3.0e-6, 15.0e-6)
        assert cost_report.rate(None) == (3.0e-6, 15.0e-6)


class TestCostOf:
    def test_component_weights(self):
        """Writes bill at 2x input, cache reads at 0.1x, output at its own rate."""
        m = "claude-sonnet-5-20250929"
        assert cost_report.cost_of(m, 1_000_000, 0, 0, 0) == pytest.approx(3.0)
        assert cost_report.cost_of(m, 0, 1_000_000, 0, 0) == pytest.approx(6.0)
        assert cost_report.cost_of(m, 0, 0, 1_000_000, 0) == pytest.approx(0.3)
        assert cost_report.cost_of(m, 0, 0, 0, 1_000_000) == pytest.approx(15.0)

    def test_zero_usage_is_free(self):
        assert cost_report.cost_of("claude-fable-5", 0, 0, 0, 0) == 0.0


USAGE = {"input_tokens": 1000, "cache_creation_input_tokens": 2000,
         "cache_read_input_tokens": 10_000, "output_tokens": 500}
# 1000*3e-6 + 2000*3e-6*2 + 10000*3e-6*0.1 + 500*15e-6
USAGE_COST = 0.0255


class TestAnalyse:
    def test_summary_for_single_thread(self, tmp_path):
        p = write_log(tmp_path / "AAA.NZ.log",
                      [assistant(usage=USAGE), result(cost=1.23, turns=7)])
        summary, rows = cost_report.analyse(p)
        assert summary["ticker"] == "AAA.NZ"
        assert summary["reported"] == 1.23
        assert summary["turns"] == 7
        assert summary["estimated"] == pytest.approx(USAGE_COST)
        assert summary["read"] == 10_000
        assert summary["out"] == 500
        assert summary["subagents"] == 0
        assert len(rows) == 1
        assert rows[0]["id"] == "MAIN"

    def test_subagents_attributed_by_parent_tool_use_id(self, tmp_path):
        """The result event covers only the MAIN thread; subagent traffic is
        recovered from parent_tool_use_id and must dominate the sort."""
        big = {"cache_read_input_tokens": 1_000_000, "output_tokens": 10_000}
        p = write_log(tmp_path / "T.log", [
            assistant(usage={"input_tokens": 10}),
            assistant(parent="toolu_01", usage=big),
            assistant(parent="toolu_01", usage=big),
            result(),
        ])
        summary, rows = cost_report.analyse(p)
        assert summary["subagents"] == 1
        assert rows[0]["id"] == "toolu_01"       # sorted by estimated cost desc
        assert rows[0]["msgs"] == 2
        assert rows[0]["read"] == 2_000_000
        assert summary["read"] == 2_000_000      # totals span all threads

    def test_subagent_count_when_no_main_messages(self, tmp_path):
        p = write_log(tmp_path / "T.log",
                      [assistant(parent="toolu_01", usage=USAGE)])
        summary, _ = cost_report.analyse(p)
        assert summary["subagents"] == 1

    def test_garbage_and_non_assistant_events_ignored(self, tmp_path):
        p = write_log(tmp_path / "T.log", [
            "not json at all",
            "{broken json",
            {"type": "system", "subtype": "init"},
            {"type": "user", "message": {"usage": {"input_tokens": 999}}},
            assistant(usage={"input_tokens": 5}),
        ])
        _summary, rows = cost_report.analyse(p)
        assert len(rows) == 1
        assert rows[0]["fresh"] == 5

    def test_incomplete_run_has_no_reported_cost(self, tmp_path):
        p = write_log(tmp_path / "T.log", [assistant(usage=USAGE)])
        summary, _ = cost_report.analyse(p)
        assert summary["reported"] is None
        assert summary["turns"] == 0

    def test_label_from_first_text_block_and_tools_counted(self, tmp_path):
        content = [
            {"type": "tool_use", "name": "Read"},
            {"type": "text", "text": "Adjudicating facts for AAA.NZ\nmore"},
            {"type": "tool_use", "name": "Read"},
            {"type": "tool_use", "name": "Bash"},
            {"type": "text", "text": "a later block must not replace it"},
        ]
        p = write_log(tmp_path / "T.log",
                      [assistant(parent="t1", content=content)])
        _, rows = cost_report.analyse(p)
        assert rows[0]["label"] == "Adjudicating facts for AAA.NZ"
        assert rows[0]["tools"] == {"Read": 2, "Bash": 1}


@pytest.fixture
def logs(monkeypatch, tmp_path):
    """Retarget cost_report.LOGS at a tmp transcript dir."""
    d = tmp_path / "logs"
    d.mkdir()
    monkeypatch.setattr(cost_report, "LOGS", d)
    return d


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["cost_report.py", *argv])
    return cost_report.main()


class TestMain:
    def test_missing_logs_dir_returns_1(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(cost_report, "LOGS", tmp_path / "absent")
        assert run_main(monkeypatch) == 1
        assert "no transcripts yet" in capsys.readouterr().err

    def test_no_matching_transcripts_returns_1(self, logs, monkeypatch, capsys):
        assert run_main(monkeypatch, "NOPE.NZ") == 1
        assert "no matching transcripts" in capsys.readouterr().err

    def test_table_prefers_reported_cost(self, logs, monkeypatch, capsys):
        write_log(logs / "AAA.NZ.log",
                  [assistant(usage=USAGE), result(cost=1.23, turns=7)])
        assert run_main(monkeypatch) == 0
        out = capsys.readouterr().out
        assert "AAA.NZ" in out
        assert "$   1.23" in out
        assert "TOTAL" in out
        assert "mean $1.23/ticker over 1" in out

    def test_table_falls_back_to_estimated(self, logs, monkeypatch, capsys):
        write_log(logs / "AAA.NZ.log", [assistant(usage=USAGE)])
        assert run_main(monkeypatch) == 0
        assert "$   0.03" in capsys.readouterr().out   # USAGE_COST rounded

    def test_named_ticker_shows_detail(self, logs, monkeypatch, capsys):
        write_log(logs / "AAA.NZ.log",
                  [assistant(usage=USAGE), result()])
        assert run_main(monkeypatch, "AAA.NZ") == 0
        out = capsys.readouterr().out
        assert "=== AAA.NZ ===" in out
        assert "MAIN THREAD" in out

    def test_detail_marks_incomplete_runs(self, logs, monkeypatch, capsys):
        write_log(logs / "AAA.NZ.log", [assistant(usage=USAGE)])
        assert run_main(monkeypatch, "AAA.NZ") == 0
        assert "(incomplete run)" in capsys.readouterr().out

    def test_baseline_writes_snapshot(self, logs, monkeypatch, tmp_path, capsys):
        write_log(logs / "AAA.NZ.log",
                  [assistant(usage=USAGE), result(cost=2.46)])
        fp = tmp_path / "base.json"
        assert run_main(monkeypatch, "--baseline", str(fp)) == 0
        assert json.loads(fp.read_text()) == {"AAA.NZ": 2.46}

    def test_baseline_without_filename_returns_2(self, logs, monkeypatch, capsys):
        write_log(logs / "AAA.NZ.log", [assistant(usage=USAGE), result()])
        assert run_main(monkeypatch, "--baseline") == 2
        assert "needs a filename" in capsys.readouterr().err

    def test_compare_missing_baseline_returns_1(self, logs, monkeypatch,
                                                tmp_path, capsys):
        write_log(logs / "AAA.NZ.log", [assistant(usage=USAGE), result()])
        assert run_main(monkeypatch, "--compare", str(tmp_path / "no.json")) == 1
        assert "no baseline" in capsys.readouterr().err

    def test_compare_reports_pct_change_and_new_tickers(self, logs, monkeypatch,
                                                        tmp_path, capsys):
        write_log(logs / "AAA.NZ.log",
                  [assistant(usage=USAGE), result(cost=1.23)])
        write_log(logs / "BBB.NZ.log",
                  [assistant(usage=USAGE), result(cost=0.50)])
        fp = tmp_path / "base.json"
        fp.write_text(json.dumps({"AAA.NZ": 2.46}))
        assert run_main(monkeypatch, "--compare", str(fp)) == 0
        out = capsys.readouterr().out
        assert "-50.0%" in out          # 2.46 -> 1.23
        assert "(new)" in out           # BBB.NZ absent from the baseline
