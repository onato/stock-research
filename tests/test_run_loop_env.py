"""run_loop.sh must not let $PARALLEL in the environment reprogram GNU parallel.

GNU parallel prepends the contents of the environment variable $PARALLEL to
its own command line. `make run PARALLEL=3` exports PARALLEL=3 (make exports
command-line variables to recipe shells), so parallel saw `3` as the command
to run and every worker failed instantly with
`bash: line 1: 3: command not found` (2026-08-24, NZX.NZ/PCT.NZ/PEB.NZ).
The advertised interface `make run TICKERS=8 PARALLEL=4` was therefore broken
for any parallelism override.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent


def make_sandbox(tmp_path):
    """A minimal repo: the real run_loop.sh, everything else stubbed."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "state").mkdir()

    shutil.copy(REPO / "scripts" / "run_loop.sh", repo / "scripts" / "run_loop.sh")

    # run_loop.sh only needs require_tools from lib.sh.
    (repo / "scripts" / "lib.sh").write_text("require_tools() { :; }\n")

    # The worker records that it ran and with which argument.
    marker = repo / "state" / "ran.txt"
    worker = repo / "scripts" / "research_one.sh"
    worker.write_text(f'#!/usr/bin/env bash\necho "RAN $1" >> "{marker}"\n')
    worker.chmod(worker.stat().st_mode | stat.S_IEXEC)

    # `uv run ... filter_tickers.py ... TICKER` -> echo the ticker back,
    # i.e. "nothing filtered out".
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/usr/bin/env bash\nfor a in "$@"; do :; done\necho "$a"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)

    return repo, bin_dir, marker


def run_loop(repo, bin_dir, extra_env):
    env = os.environ | extra_env | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        [str(repo / "scripts" / "run_loop.sh"), "--no-push", "--force", "FAKE.T"],
        capture_output=True, text=True, env=env, check=False,
    )


class TestParallelEnvVar:
    def test_worker_runs_with_parallel_var_exported(self, tmp_path):
        repo, bin_dir, marker = make_sandbox(tmp_path)
        out = run_loop(repo, bin_dir, {"PARALLEL": "3"})
        assert marker.exists(), out.stdout + out.stderr
        assert "RAN FAKE.T" in marker.read_text()
        assert out.returncode == 0, out.stdout + out.stderr
        assert "command not found" not in out.stdout + out.stderr

    def test_worker_runs_without_parallel_var(self, tmp_path):
        """Guard the sandbox itself: the clean-env run must already pass."""
        repo, bin_dir, marker = make_sandbox(tmp_path)
        out = run_loop(repo, bin_dir, {})
        assert marker.exists(), out.stdout + out.stderr
        assert "RAN FAKE.T" in marker.read_text()
        assert out.returncode == 0, out.stdout + out.stderr
