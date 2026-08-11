"""A probability-weighted intrinsic value of zero is data, not missing data.

CBD.NZ (Cannasouth) is the first ticker in the corpus whose DCF resolves to a
genuine 0.0: the company is in receivership and its creditor waterfall leaves
nil residual to ordinary equity. That is a real, hard-won valuation and the
screener must rank it as -100% upside, not silently demote it to the
"need attention" list where it reads as an unreadable DCF.

`screen.py` lives under .claude/skills/screen-investments/ rather than
scripts/, so it is loaded here by path.
"""

import argparse
import importlib.util
import json
import pathlib

import pytest

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_dcf", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen_dcf = _load()


def _args(root, **over):
    base = {"root": str(root), "only": None, "live": False,
            "stale_days": 45, "drift_pct": 10, "top": 50}
    base.update(over)
    return argparse.Namespace(**base)


def add_ticker(root, ticker, weighted_iv, price, *, valuation_date=None):
    """Write a minimal {T}/Reports/{T}_DCF.json the screener can discover."""
    reports = root / ticker / "Reports"
    reports.mkdir(parents=True)
    dcf = {
        "ticker": ticker,
        "current_price": price,
        "valuation_date": valuation_date or screen_dcf.today().isoformat(),
        "inputs": {"currency": "NZD"},
        "probability_weighted": {"weighted_iv": weighted_iv},
    }
    (reports / f"{ticker}_DCF.json").write_text(json.dumps(dcf))
    return reports


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "research"
    r.mkdir()
    return r


class TestZeroIntrinsicValue:
    def test_zero_iv_is_ranked_not_unranked(self, root):
        """A worthless-equity verdict is a conclusion, so it must rank."""
        add_ticker(root, "CBD.NZ", weighted_iv=0.0, price=0.098)

        ranked, unranked = screen_dcf.screen(_args(root))

        assert [r["ticker"] for r in ranked] == ["CBD.NZ"]
        assert unranked == []

    def test_zero_iv_yields_minus_100_percent_upside(self, root):
        add_ticker(root, "CBD.NZ", weighted_iv=0.0, price=0.098)

        ranked, _ = screen_dcf.screen(_args(root))

        assert ranked[0]["upside_pct"] == -100.0

    def test_zero_iv_carries_no_no_iv_flag(self, root):
        """NO_IV means the DCF lacked a value -- zero is a value."""
        add_ticker(root, "CBD.NZ", weighted_iv=0.0, price=0.098)

        ranked, _ = screen_dcf.screen(_args(root))

        assert "NO_IV" not in ranked[0]["flags"]

    def test_missing_iv_still_flagged_no_iv(self, root):
        """The null case must keep behaving as before."""
        add_ticker(root, "GHOST.NZ", weighted_iv=None, price=1.00)

        ranked, unranked = screen_dcf.screen(_args(root))

        assert ranked == []
        assert [r["ticker"] for r in unranked] == ["GHOST.NZ"]
        assert "NO_IV" in unranked[0]["flags"]

    def test_zero_iv_sorts_below_a_positive_upside_name(self, root):
        add_ticker(root, "AAA.NZ", weighted_iv=2.00, price=1.00)
        add_ticker(root, "CBD.NZ", weighted_iv=0.0, price=0.098)

        ranked, _ = screen_dcf.screen(_args(root))

        assert [r["ticker"] for r in ranked] == ["AAA.NZ", "CBD.NZ"]

    def test_text_report_prints_zero_iv_not_a_dash(self, root, capsys,
                                                   monkeypatch):
        """The console renderer used truthiness and printed a bare dash.

        The report is rendered inline in main(), so drive the real CLI.
        """
        add_ticker(root, "CBD.NZ", weighted_iv=0.0, price=0.098)
        monkeypatch.setattr("sys.argv", ["screen.py", "--root", str(root)])

        screen_dcf.main()
        out = capsys.readouterr().out

        assert "CBD.NZ" in out
        assert "no data" not in out
        assert "0.00" in out
