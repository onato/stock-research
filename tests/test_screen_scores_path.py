"""The leaderboard's Eval column reads the scorecards from state/scores/.

`load_scores(root)` joined `root/state/scores`, but `--root` defaults to
`research` and `state/` sits BESIDE it, so the lookup resolved to
`research/state/scores`, which does not exist. Every one of the 1,177 rows on
index.html rendered "—" in the Eval column while 11,848 scorecards sat in
`state/scores/`. `load_companies` and `load_never` in the same module already
try both locations; this pins `load_scores` to the same two-candidate rule.
"""

import importlib.util
import json
import pathlib
import sys

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_scores", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen = _load()


def write_card(scores_dir, ticker, date, summary):
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / f"{ticker}_{date}.json").write_text(json.dumps(
        {"ticker": ticker, "run_at": f"{date}T00:00:00Z",
         "checks": [], "summary": summary}))


def add_ticker(root, ticker, price=3.41, iv=13.07):
    reports = root / ticker / "Reports"
    reports.mkdir(parents=True)
    (reports / f"{ticker}_DCF.json").write_text(json.dumps({
        "ticker": ticker,
        "valuation_date": screen.today().isoformat(),
        "current_price": price,
        "inputs": {"currency": "HKD", "quote_currency": "HKD"},
        "probability_weighted": {"weighted_iv": iv},
    }))


class TestLoadScoresPath:
    def test_scores_beside_the_root_are_found(self, tmp_path):
        """The repo layout: research/ and state/ are siblings."""
        root = tmp_path / "research"
        root.mkdir()
        write_card(tmp_path / "state" / "scores", "0327.HK", "2026-10-22",
                   {"score": 0.9, "fail": 1, "warn": 0})
        write_card(tmp_path / "state" / "scores", "0327.HK", "2026-10-23",
                   {"score": 1.0, "fail": 0, "warn": 3})

        assert screen.load_scores(str(root)) == {
            "0327.HK": {"score": 1.0, "fail": 0, "warn": 3}}

    def test_scores_inside_the_root_still_win(self, tmp_path):
        root = tmp_path / "research"
        root.mkdir()
        write_card(root / "state" / "scores", "XX", "2026-10-23",
                   {"score": 0.5, "fail": 2, "warn": 0})
        write_card(tmp_path / "state" / "scores", "XX", "2026-10-23",
                   {"score": 1.0, "fail": 0, "warn": 0})

        assert screen.load_scores(str(root))["XX"]["score"] == 0.5

    def test_no_scores_anywhere_is_empty(self, tmp_path):
        root = tmp_path / "research"
        root.mkdir()
        assert screen.load_scores(str(root)) == {}

    def test_index_page_shows_the_score(self, tmp_path, monkeypatch, capsys):
        """End to end through main(): the Eval cell is the score, not a dash."""
        root = tmp_path / "research"
        add_ticker(root, "0327.HK")
        write_card(tmp_path / "state" / "scores", "0327.HK", "2026-10-23",
                   {"score": 1.0, "fail": 0, "warn": 3})
        out = tmp_path / "index.html"
        monkeypatch.setattr(sys, "argv", [
            "screen.py", "--root", str(root), "--html", str(out)])

        screen.main()

        html = out.read_text()
        row = html.split('data-search="0327.hk')[1].split("</tr>")[0]
        eval_cell = row.rsplit("<td", 1)[1]
        assert "1.0" in eval_cell
        assert "(0F/3W)" in eval_cell
        assert f'data-sort="{screen.SORT_MISSING}">—' not in eval_cell
