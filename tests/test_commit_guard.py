"""commit_ticker (scripts/lib.sh) must label partial runs honestly.

Committing a partial run's output is deliberate — extracted text is worth
keeping (research_one.sh separately fails the run so the joblog records it).
The bug this pins: a run missing its deliverables (Metrics.csv, DCF.json,
Dashboard.html) was committed as `feat: new research for T`, indistinguishable
from a complete one (APL.NZ / AOF.NZ, 2026-08-04). Partial output must be
committed as `wip:` naming what's missing.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
LIB = REPO / "scripts" / "lib.sh"

DELIVERABLES = ("Metrics.csv", "DCF.json", "Dashboard.html")


def run_commit_ticker(tmp_path, present):
    """Init a scratch repo with the given deliverables, run commit_ticker."""
    repo = tmp_path / "repo"
    reports = repo / "research" / "T" / "Reports"
    reports.mkdir(parents=True)
    (repo / "research" / "T" / "Extracted").mkdir()
    (repo / "research" / "T" / "Extracted" / "T_Annual_FY2024.txt").write_text("x")
    for d in present:
        (reports / f"T_{d}").write_text("content")
    (repo / "index.html").write_text("<html></html>")
    # commit_ticker's first `git add` names these unconditionally; a missing
    # pathspec would abort it (documented in lib.sh), so mirror the real repo.
    (repo / "state" / "scores").mkdir(parents=True)
    (repo / "state" / "scores" / "T_2026-08-04.json").write_text("{}")
    (repo / "state" / "budget.json").write_text("{}")
    (repo / "evals").mkdir()
    (repo / "evals" / "ledger.jsonl").write_text("")

    script = f"""
set -e
cd "{repo}"
git init -q -b main
git config user.email t@t && git config user.name t
git commit -q --allow-empty -m "root"
REPO_ROOT="{repo}"; LOG_DIR="{repo}/logs"; PUSH=0
. "{LIB}"
set +e
commit_ticker T
echo "rc=$?"
git log -1 --format='%s' 2>/dev/null
"""
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         check=False)
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    rc = next((ln for ln in lines if ln.startswith("rc=")), "rc=?")
    subject = lines[-1] if lines else ""
    return rc, subject, out


class TestCommitTicker:
    def test_complete_run_commits_as_feat(self, tmp_path):
        rc, subject, _ = run_commit_ticker(tmp_path, DELIVERABLES)
        assert rc == "rc=0"
        assert subject == "feat: new research for T"

    def test_partial_run_commits_as_wip_naming_missing(self, tmp_path):
        rc, subject, out = run_commit_ticker(tmp_path, ("Metrics.csv",))
        assert rc == "rc=0", out.stderr
        assert subject.startswith("wip: partial research for T"), subject
        assert "DCF.json" in subject
        assert "Dashboard.html" in subject

    def test_extraction_only_run_is_wip_not_feat(self, tmp_path):
        # The APL.NZ case: extraction succeeded, everything downstream died.
        _rc, subject, _ = run_commit_ticker(tmp_path, ())
        assert subject.startswith("wip: partial research for T"), subject
