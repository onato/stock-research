"""Price and IV cells are labelled with the QUOTE currency, not the model's.

A Chinese company listed in Hong Kong models its cash flows in CNY and is
quoted in HKD. The engine writes the canonical `weighted_iv` in the quote
currency (the CNY figure sits alongside as `weighted_iv_cny`), and the
stored `current_price` is the HKEX quote, so both the price and the IV on
the leaderboard are HKD numbers. The renderer labelled both with
`inputs.currency` whenever there was no live quote (Yahoo 429s this
machine), so all 43 HK rows in the top 50 read "0.96 CNY" against
"5.69 CNY" -- an HKD price and an HKD value wearing the wrong code.

The CCY flag has the same root: it fired when the MODEL currency differed
from the live quote, which is every dual-currency ticker, including the
hundreds whose IV is already in the quote currency. It should fire only when
the IV's own currency differs from the live quote -- the DOW.NZ / WISE.L
case, where the upside really does mix currencies.
"""

import argparse
import importlib.util
import json
import pathlib

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_qccy", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen = _load()


def _args(root, **over):
    base = {"root": str(root), "only": None, "live": False,
            "stale_days": 45, "drift_pct": 10, "top": 50}
    base.update(over)
    return argparse.Namespace(**base)


def add_ticker(root, ticker, inputs, price=0.965, pw=None):
    reports = root / ticker / "Reports"
    reports.mkdir(parents=True)
    (reports / f"{ticker}_DCF.json").write_text(json.dumps({
        "ticker": ticker,
        "valuation_date": screen.today().isoformat(),
        "current_price": price,
        "inputs": inputs,
        "probability_weighted": pw or {"weighted_iv": 5.69},
    }))


def hk_row(tmp_path, **extra):
    add_ticker(tmp_path, "1969.HK",
               {"currency": "CNY", "quote_currency": "HKD", "fx_rate": 1.1724},
               pw={"weighted_iv": 5.69, "weighted_iv_cny": 4.8515})
    ranked, _, _ = screen.screen(_args(tmp_path))
    row = ranked[0]
    row.update(extra)
    return row


class TestQuoteCurrencyOnTheRow:
    def test_row_carries_the_quote_currency(self, tmp_path):
        row = hk_row(tmp_path)
        assert row["currency"] == "CNY"
        assert row["quote_currency"] == "HKD"

    def test_quote_currency_defaults_to_the_model_currency(self, tmp_path):
        """Older DCFs carry no quote_currency; a single-currency model's
        quote currency is its own."""
        add_ticker(tmp_path, "SKL.NZ", {"currency": "NZD"})
        ranked, _, _ = screen.screen(_args(tmp_path))
        assert ranked[0]["quote_currency"] == "NZD"

    def test_prose_quote_currency_is_refused(self, tmp_path):
        add_ticker(tmp_path, "XX", {"currency": "AUD",
                                    "quote_currency": "NZD (NZX line)"})
        ranked, _, _ = screen.screen(_args(tmp_path))
        assert ranked[0]["quote_currency"] == "AUD"


class TestStoredPriceLabels:
    def test_price_and_iv_are_labelled_with_the_quote_currency(self, tmp_path):
        html = screen.row_html(1, hk_row(tmp_path), None, {})
        assert '0.96 <span class="cur">HKD</span>' in html
        assert '5.69 <span class="cur">HKD</span>' in html
        assert '<span class="cur">CNY</span>' not in html

    def test_no_ccy_flag_without_a_live_quote(self, tmp_path):
        html = screen.row_html(1, hk_row(tmp_path), None, {})
        assert "CCY(" not in html


class TestCcyFlagSemantics:
    def test_live_quote_in_the_iv_currency_is_not_flagged(self, tmp_path):
        """CNY model, HKD IV, HKD live quote: nothing is mixed."""
        html = screen.row_html(1, hk_row(tmp_path, live_currency="HKD"), None, {})
        assert "CCY(" not in html
        assert '<span class="cur">HKD</span>' in html

    def test_live_quote_in_another_currency_is_flagged(self, tmp_path):
        """HKD IV against a USD quote would mix currencies -- flag it."""
        html = screen.row_html(1, hk_row(tmp_path, live_currency="USD"), None, {})
        assert "CCY(HKD→USD)" in html

    def test_single_currency_model_keeps_the_old_behaviour(self):
        row = {"ticker": "DOW.NZ", "currency": "AUD", "quote_currency": "AUD",
               "live_currency": "NZD", "price": 0.00063, "weighted_iv": 7.8,
               "upside_pct": 26884.0, "days_old": 3, "flags": [],
               "price_src": "live"}
        html = screen.row_html(1, row, None, {})
        assert "CCY(AUD→NZD)" in html

    def test_rows_without_quote_currency_fall_back_to_currency(self):
        """Row dicts from an older last_screen.json have no quote_currency."""
        row = {"ticker": "WISE.L", "currency": "USD", "live_currency": "GBp",
               "price": 885.6, "weighted_iv": 12.29, "upside_pct": -98.6,
               "days_old": 3, "flags": [], "price_src": "live"}
        html = screen.row_html(1, row, None, {})
        assert "CCY(USD→GBp)" in html

    def test_legend_describes_the_iv_currency(self):
        meaning = dict(screen.FLAG_MEANINGS)["CCY"]
        assert "intrinsic value" in meaning
