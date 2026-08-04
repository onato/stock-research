"""The pipeline's generated commit messages follow the project convention.

CLAUDE.md mandates Conventional Commits WITHOUT the parenthesized scope
(`feat: ...`, never `feat(screener): ...`). Humans read CLAUDE.md; the
shell pipeline and CI workflow do not — their hardcoded `git commit -m`
templates are checked here instead, so a scoped prefix can never sneak
back into generated history.
"""

import re
from pathlib import Path

REPO = Path(__file__).parent.parent

SCOPED_PREFIX = re.compile(
    r"""["'](feat|fix|chore|docs|test|refactor|style|perf|ci|build)"""
    r"""\([^)]*\)!?:""")


def commit_template_files():
    yield from (REPO / "scripts").glob("*.sh")
    yield from (REPO / ".github" / "workflows").glob("*.yml")


class TestGeneratedCommitMessages:
    def test_no_parenthesized_scope_in_templates(self):
        offenders = []
        for path in commit_template_files():
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if SCOPED_PREFIX.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
        assert not offenders, (
            "commit-message templates use a parenthesized scope "
            "(CLAUDE.md forbids it):\n" + "\n".join(offenders))
