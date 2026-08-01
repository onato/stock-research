"""Shared fixtures for the scripts/ test suite.

Import path setup lives in pyproject.toml (pythonpath = ["scripts"]), which
reproduces the flat sibling-import environment the scripts run under. This
file holds only fixtures.

Every scripts/ module binds REPO = Path(__file__).resolve().parents[1] (and
paths derived from it, like ledger.LEDGER) at import time, so hermetic tests
must monkeypatch the module attributes, not a shared constant. `patch_repo`
does that for all of them at once.
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_repo(tmp_path):
    """A minimal repo skeleton (research/, state/, evals/) under tmp_path."""
    for d in ("research", "state", "evals"):
        (tmp_path / d).mkdir()
    return tmp_path


@pytest.fixture
def patch_repo(monkeypatch, tmp_repo):
    """Retarget every module-level repo path at tmp_repo; returns tmp_repo."""
    import build_facts
    import check_currency
    import dcf_fields
    import export_csv
    import guard
    import ledger
    import load_existing
    import run_evals

    for mod in (dcf_fields, build_facts, export_csv, load_existing, check_currency):
        monkeypatch.setattr(mod, "REPO", tmp_repo)
    # run_evals reads paths through dcf_fields (F.REPO) at call time, but its
    # scorecard directory is bound at import.
    monkeypatch.setattr(run_evals, "SCORES", tmp_repo / "state" / "scores")
    monkeypatch.setattr(ledger, "LEDGER", tmp_repo / "evals" / "ledger.jsonl")
    monkeypatch.setattr(ledger, "LOCK", tmp_repo / "state" / "ledger.lock")
    monkeypatch.setattr(guard, "STATE_FILE", tmp_repo / "state" / "budget.json")
    return tmp_repo


@pytest.fixture
def make_ticker(patch_repo):
    """Create research/{T}/Reports|Extracted in the patched repo."""

    def _make(ticker):
        d = patch_repo / "research" / ticker
        (d / "Reports").mkdir(parents=True, exist_ok=True)
        (d / "Extracted").mkdir(exist_ok=True)
        return d

    return _make


@pytest.fixture
def dcf():
    """Load a committed DCF fixture by name: dcf('WISE.L') or dcf('synthetic_pct_weights')."""

    def _load(name):
        return json.loads((FIXTURES / "dcf" / f"{name}.json").read_text())

    return _load


@pytest.fixture
def pinned_identity(monkeypatch):
    """Freeze agents_sha/git_head so ledger rows are deterministic."""
    import dcf_fields

    monkeypatch.setattr(dcf_fields, "agents_sha", lambda: "testsha00000")
    monkeypatch.setattr(dcf_fields, "git_head", lambda: "testhead")


@pytest.fixture
def mem_db():
    """In-memory DuckDB with the canonical schema applied."""
    import duckdb
    import schema

    con = duckdb.connect(":memory:")
    con.execute(schema.create_sql())
    yield con
    con.close()
