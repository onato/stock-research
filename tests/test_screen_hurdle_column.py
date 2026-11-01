"""The leaderboard carries a second upside column: base case at 15%.

Review of 2026-10-31: 43 of the top 50 were HK listings whose intrinsic
values implied the same ~21x earnings multiple as the US names, at a 9.5%
WACC that carries no China-specific premium. Every DCF already has a
`required_return_table` (base scenario rebuilt at 9/10/11/12/15%), so the
cheapest honest stress is to show the 15% row's value against the same
price. The HK universe median is -19% on that row; US/AX/L/TO/T/DE are
-55% to -67%. The rank stays on the scenario-weighted IV; this column is
sortable and informational, never the verdict.

A DCF without a 15% row (BRW.NZ lists 10/12.5/14.5/16.5/20) shows an em
dash that sorts to the bottom -- no interpolation, no nearest row.
"""

import argparse
import importlib.util
import json
import pathlib
from typing import ClassVar

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_hurdle", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen = _load()

TABLE = {"scenario": "base",
         "returns": [9, 10, 11, 12.0, 15],
         "value_per_share": [8.37, 7.9, 7.47, 7.07, 5.78]}


def _args(root, **over):
    base = {"root": str(root), "only": None, "live": False,
            "stale_days": 45, "drift_pct": 10, "top": 50}
    base.update(over)
    return argparse.Namespace(**base)


def add_ticker(root, ticker, table="absent", price=0.965):
    reports = root / ticker / "Reports"
    reports.mkdir(parents=True)
    dcf = {
        "ticker": ticker,
        "valuation_date": screen.today().isoformat(),
        "current_price": price,
        "inputs": {"currency": "CNY", "quote_currency": "HKD"},
        "probability_weighted": {"weighted_iv": 5.69},
    }
    if table != "absent":
        dcf["required_return_table"] = table
    (reports / f"{ticker}_DCF.json").write_text(json.dumps(dcf))


def row_for(tmp_path, table="absent", price=0.965):
    add_ticker(tmp_path, "1969.HK", table, price)
    ranked, _, _ = screen.screen(_args(tmp_path))
    return ranked[0]


class TestValueAtHurdle:
    def test_fifteen_percent_row_is_read(self, tmp_path):
        row = row_for(tmp_path, TABLE)
        assert row["iv_at_hurdle"] == 5.78
        assert row["upside_at_hurdle_pct"] == 499.0

    def test_float_rate_matches(self, tmp_path):
        table = dict(TABLE, returns=[9, 10, 11, 12, 15.0])
        assert row_for(tmp_path, table)["iv_at_hurdle"] == 5.78

    def test_no_fifteen_row_is_none(self, tmp_path):
        table = {"returns": [10, 12.5, 14.5, 16.5, 20],
                 "value_per_share": [3.0, 2.5, 2.1, 1.8, 1.4]}
        row = row_for(tmp_path, table)
        assert row["iv_at_hurdle"] is None
        assert row["upside_at_hurdle_pct"] is None

    def test_absent_table_is_none(self, tmp_path):
        assert row_for(tmp_path)["upside_at_hurdle_pct"] is None

    def test_ragged_table_is_none(self, tmp_path):
        table = {"returns": [9, 15], "value_per_share": [8.37]}
        assert row_for(tmp_path, table)["iv_at_hurdle"] is None

    def test_non_numeric_value_is_none(self, tmp_path):
        table = {"returns": [15], "value_per_share": ["n/a"]}
        assert row_for(tmp_path, table)["iv_at_hurdle"] is None

    def test_no_price_is_none(self, tmp_path):
        add_ticker(tmp_path, "1969.HK", TABLE, price=None)
        _, unranked, _ = screen.screen(_args(tmp_path))   # NO_PRICE: unranked
        row = unranked[0]
        assert row["iv_at_hurdle"] == 5.78
        assert row["upside_at_hurdle_pct"] is None

    def test_rank_is_still_the_weighted_upside(self, tmp_path):
        """A name that survives 15% but has less weighted upside stays below."""
        add_ticker(tmp_path, "AAA", TABLE, price=0.965)            # +489.6% / +499%
        add_ticker(tmp_path, "BBB", dict(TABLE, value_per_share=[9, 9, 9, 9, 9]),
                   price=1.0)                                      # +469% / +800%
        ranked, _, _ = screen.screen(_args(tmp_path))
        assert [r["ticker"] for r in ranked] == ["AAA", "BBB"]

    def test_hurdle_rate_is_fifteen(self):
        assert screen.HURDLE_RATE == 15.0


class TestHurdleColumn:
    ROW: ClassVar[dict] = {
           "ticker": "1969.HK", "currency": "CNY", "quote_currency": "HKD",
           "price": 0.965, "weighted_iv": 5.69, "upside_pct": 489.6,
           "days_old": 3, "flags": [], "price_src": "stored",
           "iv_at_hurdle": 5.78, "upside_at_hurdle_pct": 499.0}

    def cell(self, row):
        html = screen.row_html(1, row, None, {})
        # after #, ticker, company, price, IV, upside
        return html.split("</td>")[6]

    def test_header_has_the_column_after_upside(self, tmp_path):
        out = tmp_path / "index.html"
        screen.write_html([self.ROW], [], [],
                          {"generated_at": "x", "live": False}, {}, {}, str(out))
        html = out.read_text()
        assert ('<th data-type="num">Upside</th>'
                '<th data-type="num">Upside @15%</th>') in html

    def test_cell_renders_signed_percent(self):
        cell = self.cell(self.ROW)
        assert "+499.0%" in cell
        assert 'data-sort="499.0"' in cell
        assert 'class="pos"' in cell
        assert "5.78" in cell          # the value itself, as the tooltip

    def test_negative_is_red(self):
        cell = self.cell(dict(self.ROW, iv_at_hurdle=0.5,
                              upside_at_hurdle_pct=-48.2))
        assert "-48.2%" in cell
        assert 'class="neg"' in cell

    def test_missing_is_dash_and_sorts_last(self):
        cell = self.cell(dict(self.ROW, iv_at_hurdle=None,
                              upside_at_hurdle_pct=None))
        assert "—" in cell
        assert f'data-sort="{screen.SORT_MISSING}"' in cell

    def test_old_row_dicts_without_the_key_still_render(self):
        row = {k: v for k, v in self.ROW.items()
               if k not in ("iv_at_hurdle", "upside_at_hurdle_pct")}
        assert "—" in self.cell(row)

    def test_footnote_explains_the_column(self, tmp_path):
        out = tmp_path / "index.html"
        screen.write_html([self.ROW], [], [],
                          {"generated_at": "x", "live": False}, {}, {}, str(out))
        html = out.read_text()
        legend = html.rsplit('class="footnote"', 1)[1]
        assert "Upside @15%" in legend
        assert "required return" in legend
