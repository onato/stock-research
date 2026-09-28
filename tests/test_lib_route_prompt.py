"""lib.sh's route_prompt: which skill prompt a ticker gets, and the REVALUE override.

refresh_route.py sends a ticker whose filings are parsed and whose DCF is
recent to "no model" (tier 1). That is right for a nightly run, but it left no
way to re-run just the valuation on a freshly researched ticker: on 2026-09-26
89 DCFs had been built on Sonnet after the Fable limit ran out, and every one
of them routed to "skip", and /refresh-stock would have kept each Sonnet DCF
whenever the narrative had not moved. REVALUE=1 bypasses the router and sends
the ticker to /revalue-stock, which rebuilds only the DCF.
"""

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent


def route(tmp_path, env_extra):
    """Source the real lib.sh with a stub `uv` that echoes its argv back."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/usr/bin/env bash\necho "ARGS $*"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    base = {k: v for k, v in os.environ.items() if k not in ("FORCE", "REVALUE")}
    env = base | {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                  "REPO_ROOT": str(REPO), "LOG_DIR": str(tmp_path)} | env_extra
    return subprocess.run(
        ["bash", "-c", f'. "{REPO}/scripts/lib.sh"; route_prompt FAKE.T'],
        capture_output=True, text=True, env=env, check=False,
    )


def test_default_asks_the_router_with_no_overrides(tmp_path):
    out = route(tmp_path, {})
    assert out.returncode == 0
    assert "--ticker FAKE.T --prompt" in out.stdout
    assert "--force" not in out.stdout


def test_revalue_sends_the_ticker_to_the_dcf_only_skill(tmp_path):
    out = route(tmp_path, {"REVALUE": "1"})
    assert out.returncode == 0
    assert out.stdout.strip() == "/revalue-stock FAKE.T"


def test_force_still_pins_the_full_research(tmp_path):
    out = route(tmp_path, {"FORCE": "1"})
    assert "--force" in out.stdout
