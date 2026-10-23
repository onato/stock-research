"""commit_kpi_promotions (scripts/lib.sh) commits the batch's shared KPI edits.

The financial-parser adds each ticker's new KPIs to scripts/schema.py's
PROMOTE_KPIS, with tests in tests/test_schema.py and tests/test_kpi_coverage.py.
commit_ticker only stages research/{T} and the state/ outputs, and
commit-scores only the scorecards, so these edits were never committed by
`make run` -- they piled up in the working tree and needed a hand commit
after every HK batch (48ef9a56, then again 2026-10-23).

Several tickers edit these files concurrently, so they are committed once,
at the end of the batch, and only when the test gates pass. They are
committed by explicit pathspec so nothing else staged rides along.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
LIB = REPO / "scripts" / "lib.sh"
KPI_FILES = ("scripts/schema.py", "tests/test_schema.py", "tests/test_kpi_coverage.py")


def run(tmp_path, gate, modify=KPI_FILES, extra=()):
    repo = tmp_path / "repo"
    for f in (*KPI_FILES, "scripts/other.py", "notes.txt"):
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("v1\n")
    edits = "\n".join(f'echo v2 >> "{f}"' for f in (*modify, *extra))
    script = f"""
set -e
cd "{repo}"
git init -q -b main
git config user.email t@t && git config user.name t
git add -A && git commit -q -m "root"
{edits}
# Someone else's staged work must not ride along in the KPI commit.
echo staged >> notes.txt && git add notes.txt
REPO_ROOT="{repo}"; LOG_DIR="{repo}/logs"; PUSH=0
KPI_GATE="{gate}"
. "{LIB}"
set +e
commit_kpi_promotions
echo "rc=$?"
"""
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         check=False)

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True,
                              text=True, check=False).stdout

    return out, git


class TestCommitKpiPromotions:
    def test_passing_gate_commits_only_the_kpi_files(self, tmp_path):
        out, git = run(tmp_path, "true", extra=("scripts/other.py",))
        assert "rc=0" in out.stdout, out.stderr
        assert git("log", "-1", "--format=%s").strip() == (
            "feat: promote batch KPIs to the Metrics CSV")
        committed = set(git("show", "--name-only", "--format=", "HEAD").split())
        assert committed == set(KPI_FILES)
        # The unrelated edit and the other staged file are left as they were.
        status = git("status", "--porcelain")
        assert " M scripts/other.py" in status
        assert "M  notes.txt" in status

    def test_failing_gate_leaves_the_edits_uncommitted(self, tmp_path):
        out, git = run(tmp_path, "false")
        assert "rc=0" in out.stdout, out.stderr
        assert git("log", "-1", "--format=%s").strip() == "root"
        assert "not committed" in out.stderr
        assert " M scripts/schema.py" in git("status", "--porcelain")

    def test_a_partial_set_of_kpi_edits_is_still_committed(self, tmp_path):
        out, git = run(tmp_path, "true", modify=("scripts/schema.py",))
        assert "rc=0" in out.stdout, out.stderr
        assert git("show", "--name-only", "--format=", "HEAD").split() == [
            "scripts/schema.py"]

    def test_nothing_to_commit_skips_the_gate(self, tmp_path):
        out, git = run(tmp_path, "echo GATE-RAN; false", modify=())
        assert "rc=0" in out.stdout, out.stderr
        assert "GATE-RAN" not in out.stdout
        assert git("log", "-1", "--format=%s").strip() == "root"
