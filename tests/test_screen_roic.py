"""The index leaderboard carries a ROIC column (ROE for financials).

The number comes from dashboard_spec.returns_for -- the same function the
dashboard's returns card uses -- so the two pages cannot disagree. A ticker
with no Metrics.csv, or no statutory tax rate on file, shows an em dash that
sorts to the bottom, never an assumed value.
"""

import importlib.util
import pathlib

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_roic", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen = _load()

ROW = {"ticker": "SYN.NZ", "currency": "NZD", "price": 1.0, "weighted_iv": 2.0,
       "upside_pct": 100.0, "days_old": 3, "flags": [], "price_src": "stored"}


def roic_cell(ret):
    html = screen.row_html(1, ROW, None, {}, ret)
    return html.split("</td>")[6]   # after #, ticker, company, price, IV, upside


def test_header_has_roic_column(tmp_path):
    out = tmp_path / "index.html"
    screen.write_html([ROW], [], [], {"generated_at": "x", "live": False}, {}, {}, str(out))
    assert '<th data-type="num">ROIC</th>' in out.read_text()


def test_operating_roic_renders_percent():
    cell = roic_cell({"measure": "ROIC", "latest": 18.43, "avg5": 16.2,
                      "latest_period": "FY2025", "thin_capital": False})
    assert 'data-sort="18.43"' in cell
    assert "18.4%" in cell
    assert "FY2025" in cell
    assert "16.2%" in cell
    assert 'class="pos"' in cell


def test_financial_is_tagged_roe():
    cell = roic_cell({"measure": "ROE", "latest": 11.0, "avg5": None,
                      "latest_period": "FY2025", "thin_capital": False})
    assert "11.0%" in cell
    assert "ROE" in cell


def test_thin_capital_is_marked():
    cell = roic_cell({"measure": "ROIC", "latest": 90.0, "avg5": None,
                      "latest_period": "FY2025", "thin_capital": True})
    assert "*" in cell


def test_missing_is_dash_and_sorts_last():
    for ret in (None, {"measure": "ROIC", "latest": None, "avg5": None,
                       "latest_period": None, "thin_capital": False}):
        cell = roic_cell(ret)
        assert f'data-sort="{screen.SORT_MISSING}"' in cell
        assert "—" in cell


def test_negative_roic_is_red():
    cell = roic_cell({"measure": "ROIC", "latest": -5.0, "avg5": None,
                      "latest_period": "FY2025", "thin_capital": False})
    assert 'class="neg"' in cell
