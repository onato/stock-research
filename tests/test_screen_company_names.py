"""The leaderboard must show company names, not 129 blank cells.

`state/companies.json` is a SIBLING of `research/`, not a child of it, but
`load_companies` joined it onto `--root` -- which defaults to `research`.
So it looked for `research/state/companies.json`, always missed, and every
row on the generated index rendered its company as an em dash. The bug is
invisible from the row count (all 129 tickers were present and correctly
ranked), which is why it survived: only the name column was empty.

`screen.py` lives under .claude/skills/screen-investments/ rather than
scripts/, so it is loaded here by path.
"""

import importlib.util
import json
import pathlib

SCREEN_PY = (pathlib.Path(__file__).resolve().parents[1]
             / ".claude" / "skills" / "screen-investments" / "screen.py")


def _load():
    spec = importlib.util.spec_from_file_location("screen_names", SCREEN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen = _load()

COMPANIES = {
    "NZK.NZ": {"name": "New Zealand King Salmon Investments Limited",
               "sector": "Aquaculture / Food Producers"},
}


def _repo(tmp_path):
    """A repo laid out like the real one: state/ beside research/."""
    (tmp_path / "research").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "companies.json").write_text(json.dumps(COMPANIES))
    return tmp_path


def test_companies_load_when_root_is_the_research_dir(tmp_path):
    """The real invocation: --root defaults to `research`, state/ is above it."""
    repo = _repo(tmp_path)
    got = screen.load_companies(str(repo / "research"))
    assert got["NZK.NZ"]["name"] == "New Zealand King Salmon Investments Limited"


def test_companies_load_when_root_is_the_repo(tmp_path):
    """Passing the repo root must keep working -- that path already resolved."""
    repo = _repo(tmp_path)
    got = screen.load_companies(str(repo))
    assert got["NZK.NZ"]["sector"] == "Aquaculture / Food Producers"


def test_missing_file_is_not_an_error(tmp_path):
    """A repo with no companies.json just means bare tickers on the page."""
    (tmp_path / "research").mkdir()
    assert screen.load_companies(str(tmp_path / "research")) == {}
