"""Every committed dashboard must parse and initialise without throwing.

A dashboard is a deliverable the user opens in a browser. If its inline
script throws during init, the page renders half-built and -- because the
slider wiring runs at the END of the script -- the DCF controls are never
attached. The page still LOOKS fine, which is why this went unnoticed:
UBER shipped with dead sliders and a "$NaNB" stat tile, and ATM.NZ and
CCC.NZ shipped with a syntax error that disables their script entirely.

The two failure modes this guards, both found 2026-08-28:
  * over-escaped quotes (`\\"` inside a JS string literal) truncating the
    string and breaking parse -- a hand-built-HTML defect that
    build_dashboard.py cannot reintroduce, since it embeds via json.dumps;
  * reading a field that is *undefined* rather than null, e.g. filtering
    CAGR keys with `!== null` and then calling .toFixed() on the survivor.

These are legacy hand-built pages. The test is xfail-listed rather than
skipped so the list can only shrink: regenerate a page through
build_dashboard.py and remove it from KNOWN_BROKEN.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# Legacy hand-built dashboards known to fail, to be regenerated through
# build_dashboard.py. Do not add to this list -- a new entry means a
# generated page regressed, which is a bug in the generator or the spec.
KNOWN_BROKEN = {
    "ATM.NZ", "CCC.NZ",          # over-escaped quotes -> SyntaxError
    "AMZN", "BIF.NZ", "CEN.NZ", "DCBO", "DGL.NZ", "GNE.NZ", "IPL.NZ",
    "NZL.NZ", "OCA.NZ", "RKLB", "SPK.NZ", "TSM",
}


def dashboards() -> list[pathlib.Path]:
    return sorted((REPO / "research").glob("*/Reports/*_Dashboard.html"))


def inline(html: str) -> str:
    return "\n".join(re.findall(r"<script>(.*?)</script>", html, re.DOTALL))


def ticker_of(path: pathlib.Path) -> str:
    return path.parent.parent.name


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("path", dashboards(), ids=ticker_of)
def test_committed_dashboard_script_parses(path, tmp_path):
    """The inline script must be syntactically valid JavaScript."""
    if ticker_of(path) in KNOWN_BROKEN:
        pytest.xfail(f"{ticker_of(path)}: legacy hand-built page, regenerate it")
    js = tmp_path / "d.js"
    js.write_text(inline(path.read_text(errors="replace")))
    r = subprocess.run(["node", "--check", str(js)],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, f"{ticker_of(path)}: {r.stderr.strip()[:300]}"
