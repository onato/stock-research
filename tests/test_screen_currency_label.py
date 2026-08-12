"""The currency column shows a currency code, not a prose description.

Two corpus DCFs describe their mixed denomination in `inputs.currency` as
free text -- HFL.NZ carries "NZD (outputs) / GBP (fundamentals)" and AFI.NZ
"AUD (fundamentals) / NZD (reported valuation outputs)". The screener reads
that field verbatim into the price cell and into the CCY flag, so one row
renders a 34-character string where every other row shows three, and the
whole leaderboard table is stretched past the viewport.

Both files carry a clean top-level `currency: "NZD"`, so the code is
available; the renderer just has to prefer it and refuse to print prose.
"""

import argparse
import importlib.util
import json
import pathlib

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_ccy", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen_dcf = _load()


def _args(root, **over):
    base = {"root": str(root), "only": None, "live": False,
            "stale_days": 45, "drift_pct": 10, "top": 50}
    base.update(over)
    return argparse.Namespace(**base)


def add_ticker(root, ticker, *, inputs_currency, top_currency):
    reports = root / ticker / "Reports"
    reports.mkdir(parents=True)
    (reports / f"{ticker}_DCF.json").write_text(json.dumps({
        "ticker": ticker,
        "valuation_date": "2026-08-01",
        "current_price": 6.01,
        "currency": top_currency,
        "inputs": {"currency": inputs_currency},
        "probability_weighted": {"weighted_iv": 5.41},
    }))


class TestCurrencyLabel:
    def test_prose_currency_is_not_rendered(self, tmp_path):
        """The HFL.NZ row must not carry a 34-character currency cell."""
        add_ticker(tmp_path, "HFL.NZ",
                   inputs_currency="NZD (outputs) / GBP (fundamentals)",
                   top_currency="NZD")
        rows, _unranked = screen_dcf.screen(_args(tmp_path))
        assert rows[0]["currency"] == "NZD"

    def test_afi_shape_also_resolves(self, tmp_path):
        add_ticker(tmp_path, "AFI.NZ",
                   inputs_currency=(
                       "AUD (fundamentals) / NZD (reported valuation outputs)"),
                   top_currency="NZD")
        rows, _unranked = screen_dcf.screen(_args(tmp_path))
        assert rows[0]["currency"] == "NZD"

    def test_plain_code_is_untouched(self, tmp_path):
        add_ticker(tmp_path, "DCBO", inputs_currency="USD",
                   top_currency="USD")
        rows, _unranked = screen_dcf.screen(_args(tmp_path))
        assert rows[0]["currency"] == "USD"

    def test_inputs_code_still_wins_when_it_is_a_code(self, tmp_path):
        """inputs.currency stays the primary source when it is usable."""
        add_ticker(tmp_path, "XX", inputs_currency="EUR", top_currency="USD")
        rows, _unranked = screen_dcf.screen(_args(tmp_path))
        assert rows[0]["currency"] == "EUR"

    def test_prose_with_no_top_level_falls_back_to_none(self, tmp_path):
        """Never print prose: with no code anywhere, show nothing."""
        add_ticker(tmp_path, "YY",
                   inputs_currency="NZD (outputs) / GBP (fundamentals)",
                   top_currency=None)
        rows, _unranked = screen_dcf.screen(_args(tmp_path))
        assert rows[0]["currency"] in (None, "")

    def test_rendered_html_has_no_prose_currency(self, tmp_path):
        add_ticker(tmp_path, "HFL.NZ",
                   inputs_currency="NZD (outputs) / GBP (fundamentals)",
                   top_currency="NZD")
        rows, _unranked = screen_dcf.screen(_args(tmp_path))
        html = screen_dcf.row_html(1, rows[0], "", {})
        assert "(fundamentals)" not in html
        assert "(outputs)" not in html
