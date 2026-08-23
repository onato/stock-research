"""Cigar butts and dead shells are filtered from the ranking, not ranked.

BGI.NZ is a frozen RTO shell whose stale $0.004 print made it the screener's
#1 pick at +980% "upside" -- the top of the leaderboard was a company that
will never be bought. Rows are moved to an `excluded` bucket ("not under
consideration") when the DCF itself says the valuation is terminal (shell /
liquidation / going-concern models), when weighted IV is zero or negative
(equity worthless), or when the ticker is listed in
state/never_interested.txt with a reason.

`screen.py` lives under .claude/skills/screen-investments/, loaded by path.
"""

import argparse
import importlib.util
import json
import pathlib

import pytest

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_excl", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen_mod = _load()


def _args(root, **over):
    base = {"root": str(root), "only": None, "live": False,
            "stale_days": 45, "drift_pct": 10, "top": 50}
    base.update(over)
    return argparse.Namespace(**base)


def add_ticker(root, ticker, weighted_iv, price, *, method=None):
    reports = root / ticker / "Reports"
    reports.mkdir(parents=True)
    dcf = {
        "ticker": ticker,
        "current_price": price,
        "valuation_date": screen_mod.today().isoformat(),
        "inputs": {"currency": "NZD"},
        "probability_weighted": {"weighted_iv": weighted_iv},
    }
    if method:
        dcf["valuation_method"] = method
    (reports / f"{ticker}_DCF.json").write_text(json.dumps(dcf))
    return reports


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "research"
    r.mkdir()
    return r


class TestTerminalModels:
    def test_shell_model_is_excluded_even_with_huge_upside(self, root):
        # The BGI.NZ case: positive IV against a frozen tick-floor price.
        add_ticker(root, "BGI.NZ", weighted_iv=0.043, price=0.004,
                   method="asset_scenario_weighted_shell")

        ranked, unranked, excluded = screen_mod.screen(_args(root))

        assert ranked == []
        assert unranked == []
        assert [r["ticker"] for r in excluded] == ["BGI.NZ"]
        assert "shell" in excluded[0]["excluded_reason"]

    def test_liquidation_model_is_excluded(self, root):
        add_ticker(root, "CBD.NZ", weighted_iv=0.0, price=0.098,
                   method="liquidation_waterfall")

        _, _, excluded = screen_mod.screen(_args(root))

        assert [r["ticker"] for r in excluded] == ["CBD.NZ"]

    def test_going_concern_risk_model_is_excluded(self, root):
        add_ticker(root, "NTL.NZ", weighted_iv=0.008, price=0.008,
                   method="asset_scenario_weighted_going_concern_risk")

        _, _, excluded = screen_mod.screen(_args(root))

        assert [r["ticker"] for r in excluded] == ["NTL.NZ"]


class TestWorthlessEquity:
    def test_negative_iv_is_excluded(self, root):
        add_ticker(root, "AGL.NZ", weighted_iv=-0.10, price=0.17)

        ranked, _, excluded = screen_mod.screen(_args(root))

        assert ranked == []
        assert [r["ticker"] for r in excluded] == ["AGL.NZ"]
        assert "worthless" in excluded[0]["excluded_reason"]

    def test_excluded_rows_keep_their_computed_upside(self, root):
        # The verdict stays visible in the bottom section -- it is a
        # conclusion, not missing data.
        add_ticker(root, "AGL.NZ", weighted_iv=-0.10, price=0.17)

        _, _, excluded = screen_mod.screen(_args(root))

        assert excluded[0]["upside_pct"] is not None


class TestNeverFile:
    def test_listed_ticker_is_excluded_with_its_reason(self, root):
        add_ticker(root, "MEE.NZ", weighted_iv=0.10, price=0.068)
        state = root.parent / "state"
        state.mkdir()
        (state / "never_interested.txt").write_text(
            "# comment line\n"
            "MEE.NZ  two balance-date changes and ~500x consolidations\n")

        ranked, _, excluded = screen_mod.screen(_args(root))

        assert ranked == []
        assert [r["ticker"] for r in excluded] == ["MEE.NZ"]
        assert "consolidations" in excluded[0]["excluded_reason"]

    def test_missing_file_excludes_nothing(self, root):
        add_ticker(root, "AAA.NZ", weighted_iv=2.0, price=1.0)

        ranked, _, excluded = screen_mod.screen(_args(root))

        assert [r["ticker"] for r in ranked] == ["AAA.NZ"]
        assert excluded == []


class TestHealthyRowsUntouched:
    def test_ordinary_negative_upside_still_ranks(self, root):
        # Overpriced is not the same as dead: IV > 0 with IV < price ranks.
        add_ticker(root, "AIA.NZ", weighted_iv=1.90, price=8.76)

        ranked, _, excluded = screen_mod.screen(_args(root))

        assert [r["ticker"] for r in ranked] == ["AIA.NZ"]
        assert excluded == []


class TestRendering:
    def test_html_lists_excluded_at_the_bottom(self, root, tmp_path,
                                               monkeypatch):
        add_ticker(root, "AAA.NZ", weighted_iv=2.0, price=1.0)
        add_ticker(root, "BGI.NZ", weighted_iv=0.043, price=0.004,
                   method="asset_scenario_weighted_shell")
        out = tmp_path / "index.html"
        monkeypatch.setattr("sys.argv", ["screen.py", "--root", str(root),
                                         "--html", str(out)])

        screen_mod.main()
        html_text = out.read_text()

        assert "Not under consideration" in html_text
        assert html_text.index("AAA.NZ") < html_text.index("Not under consideration")
        assert "BGI.NZ" in html_text[html_text.index("Not under consideration"):]

    def test_console_names_the_excluded(self, root, capsys, monkeypatch):
        add_ticker(root, "BGI.NZ", weighted_iv=0.043, price=0.004,
                   method="asset_scenario_weighted_shell")
        monkeypatch.setattr("sys.argv", ["screen.py", "--root", str(root)])

        screen_mod.main()
        out = capsys.readouterr().out

        assert "BGI.NZ" in out
        assert "consideration" in out.lower()
