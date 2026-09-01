"""A ticker with no built dashboard must not be linked to one.

The screener listed 180 tickers and linked every one of them to
`research/{T}/Reports/{T}_Dashboard.html`, whether or not that file had ever
been built. 33 of those files did not exist -- not on disk, not in git -- so
the published site served 33 links straight to a 404. 24 sat in the
"Not ranked" section (which is *why* they have no dashboard: no DCF, or no
metrics at all) and 9 were ranked rows whose dashboard was simply never
generated.

The href is emitted in four places -- the row's `data-href` click-through and
three `<a>` cells across the ranked, unranked and excluded tables -- so the
existence check belongs in the helpers both of those go through, not at the
call sites.

Only the link is suppressed. The row keeps its ticker, its numbers and its
place in the ranking: not having a dashboard is a gap in the site, not a
reason to hide a company from the screen.

`screen.py` lives under .claude/skills/screen-investments/ rather than
scripts/, so it is loaded here by path.
"""

import importlib.util
import pathlib

import pytest

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_missing_dash", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


def _make_dashboard(root: pathlib.Path, ticker: str) -> None:
    d = root / ticker / "Reports"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ticker}_Dashboard.html").write_text("<html></html>")


@pytest.fixture
def rooted(mod, tmp_path, monkeypatch):
    """Point the module's link helpers at a temp research root.

    HREF_PREFIX is what lands in the HTML; ROOT_DIR is where existence is
    checked. main() sets both from --root, and they are deliberately
    separate: the href is relative to the published page, the check is
    relative to the filesystem.
    """
    monkeypatch.setattr(mod, "HREF_PREFIX", "research/", raising=False)
    monkeypatch.setattr(mod, "ROOT_DIR", str(tmp_path), raising=False)
    return tmp_path


def test_links_a_ticker_whose_dashboard_exists(mod, rooted):
    _make_dashboard(rooted, "SKC.NZ")
    assert mod.has_dashboard("SKC.NZ") is True
    assert mod.dashboard_href("SKC.NZ") == "research/SKC.NZ/Reports/SKC.NZ_Dashboard.html"


def test_does_not_link_a_ticker_whose_dashboard_is_missing(mod, rooted):
    # Nothing created on disk: this is FBU.NZ's situation.
    assert mod.has_dashboard("FBU.NZ") is False


def test_ticker_cell_is_a_link_only_when_the_file_exists(mod, rooted):
    _make_dashboard(rooted, "SKC.NZ")

    linked = mod.ticker_td("SKC.NZ")
    assert "<a href=" in linked
    assert "SKC.NZ_Dashboard.html" in linked
    assert "SKC.NZ" in linked

    plain = mod.ticker_td("FBU.NZ")
    assert "<a" not in plain, "a missing dashboard must not be linked"
    assert "href" not in plain
    assert "FBU.NZ" in plain, "the ticker itself must still be shown"


def test_row_carries_no_click_through_when_the_dashboard_is_missing(mod, rooted):
    _make_dashboard(rooted, "SKC.NZ")
    co = {"name": "SkyCity", "sector": "Casinos"}

    present = mod.tr_open("SKC.NZ", co)
    assert 'data-href="research/SKC.NZ/Reports/SKC.NZ_Dashboard.html"' in present

    absent = mod.tr_open("FBU.NZ", {"name": "Fletcher Building", "sector": "Materials"})
    assert "data-href" not in absent, "row click-through would 404"
    # The row must still be searchable by name, or the ticker vanishes from
    # the filter box.
    assert "data-search=" in absent
    assert "fletcher building" in absent


def test_ticker_is_escaped_when_unlinked(mod, rooted):
    """The unlinked branch must escape like the linked one."""
    out = mod.ticker_td("A&B<X>")
    assert "&amp;" in out
    assert "<X>" not in out


def test_root_dir_is_independent_of_href_prefix(mod, tmp_path, monkeypatch):
    """A published href is relative; the existence check is not.

    `--root .` publishes bare "TICKER/Reports/..." hrefs while the files still
    live under the real root, so a check driven off HREF_PREFIX would look in
    the wrong place and unlink every dashboard.
    """
    monkeypatch.setattr(mod, "HREF_PREFIX", "", raising=False)
    monkeypatch.setattr(mod, "ROOT_DIR", str(tmp_path), raising=False)
    _make_dashboard(tmp_path, "SKC.NZ")

    assert mod.dashboard_href("SKC.NZ") == "SKC.NZ/Reports/SKC.NZ_Dashboard.html"
    assert mod.has_dashboard("SKC.NZ") is True
