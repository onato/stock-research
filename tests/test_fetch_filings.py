"""Deterministic filing fetch orchestrator (scripts/fetch_filings.py).

Dispatch by suffix to an exchange adapter, stage the downloads, run every
staged file past the gate, promote the survivors into research/{T}/PDFs and
extract them. The ir-scraper agent is only spawned for what this reports as
missing, or when it exits 1. No test touches the network: every adapter is
monkeypatched.
"""

import json
from pathlib import Path

import fetch_filings
import pytest

FIX_PDF = Path(__file__).parent / "fixtures" / "pdf" / "synthetic_annual.pdf"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo skeleton with fetch_filings retargeted at it."""
    (tmp_path / "state").mkdir()
    (tmp_path / "research").mkdir()
    (tmp_path / "state" / "companies.json").write_text(
        json.dumps({"SYN.NZ": {"name": "Synthetic Holdings Limited"},
                    "SYN.HK": {"name": "Synthetic Holdings Limited"},
                    "SYN.AX": {"name": "Synthetic Holdings Limited"},
                    "SYN.L": {"name": "Synthetic Holdings Limited"}}))
    monkeypatch.setattr(fetch_filings, "REPO", tmp_path)
    return tmp_path


@pytest.fixture
def no_extract(monkeypatch):
    """Record extraction instead of shelling out to pdftotext."""
    calls = []
    monkeypatch.setattr(fetch_filings, "extract",
                        lambda pdf, out: calls.append((pdf.name, out.name)))
    return calls


def stage_good(dest: Path, name: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / name
    out.write_bytes(FIX_PDF.read_bytes())
    return out


class TestDispatch:
    def test_nz_goes_to_the_nzx_adapter(self, repo, monkeypatch, no_extract):
        seen = {}

        def fake(ticker, dest, after_year, **kw):
            seen.update(ticker=ticker, dest=dest, after_year=after_year)
            return 0

        monkeypatch.setattr(fetch_filings.nzx, "fetch", fake)
        monkeypatch.setattr(fetch_filings.annualreports, "fetch",
                            lambda *a, **k: pytest.fail("seed should not run here"))
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        assert fetch_filings.main(["SYN.NZ"]) == 0
        assert seen["ticker"] == "SYN.NZ"
        assert seen["dest"] == repo / "state" / "staging" / "SYN.NZ"

    def test_hk_goes_to_the_hkex_adapter(self, repo, monkeypatch, no_extract):
        seen = {}
        monkeypatch.setattr(fetch_filings.hkex, "fetch",
                            lambda ticker, dest, after_year, **kw: seen.update(
                                ticker=ticker) or 0)
        assert fetch_filings.main(["SYN.HK"]) == 0
        assert seen["ticker"] == "SYN.HK"

    def test_ax_delegates_to_fetch_asx_main(self, repo, monkeypatch, no_extract):
        argv = {}
        monkeypatch.setattr(fetch_filings.fetch_asx, "main",
                            lambda a: argv.update(args=a) or 0)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        assert fetch_filings.main(["SYN.AX", "--years", "2019-2026"]) == 0
        assert argv["args"][0] == "SYN.AX"
        assert "--years" in argv["args"]
        assert "2019-2026" in argv["args"]

    def test_an_unsupported_suffix_exits_one(self, repo, capsys):
        assert fetch_filings.main(["DUOL"]) == 1
        assert "no deterministic adapter" in capsys.readouterr().out

    def test_a_thin_ticker_also_gets_the_annualreports_seed(self, repo, monkeypatch,
                                                            no_extract):
        seeded = []
        monkeypatch.setattr(fetch_filings.nzx, "fetch", lambda *a, **k: 0)
        monkeypatch.setattr(fetch_filings.annualreports, "fetch",
                            lambda ticker, *a, **k: seeded.append(ticker) or 0)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 1)
        fetch_filings.main(["SYN.NZ"])
        assert seeded == ["SYN.NZ"]

    def test_a_well_stocked_ticker_skips_the_seed(self, repo, monkeypatch, no_extract):
        seeded = []
        monkeypatch.setattr(fetch_filings.nzx, "fetch", lambda *a, **k: 0)
        monkeypatch.setattr(fetch_filings.annualreports, "fetch",
                            lambda ticker, *a, **k: seeded.append(ticker) or 0)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 3)
        fetch_filings.main(["SYN.NZ"])
        assert seeded == []

    def test_hk_never_gets_the_annualreports_seed(self, repo, monkeypatch, no_extract):
        """The archive has no HKEX exchange code — seeding .HK would only
        produce 404s."""
        monkeypatch.setattr(fetch_filings.hkex, "fetch", lambda *a, **k: 0)
        monkeypatch.setattr(fetch_filings.annualreports, "fetch",
                            lambda *a, **k: pytest.fail("no archive for .HK"))
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 0)
        assert fetch_filings.main(["SYN.HK"]) == 0


class TestHoldingsCount:
    def test_counts_pdfs_and_extracted_text(self, repo):
        d = repo / "research" / "SYN.NZ"
        (d / "PDFs").mkdir(parents=True)
        (d / "Extracted").mkdir()
        (d / "PDFs" / "SYN.NZ_Annual_FY2024.pdf").write_bytes(b"%PDF-")
        (d / "Extracted" / "SYN.NZ_Annual_FY2024.txt").write_text("x")
        (d / "Extracted" / "SYN.NZ_Annual_FY2023.txt").write_text("x")
        assert fetch_filings.holdings_count("SYN.NZ") == 2   # distinct stems

    def test_an_unresearched_ticker_holds_nothing(self, repo):
        assert fetch_filings.holdings_count("SYN.NZ") == 0


class TestPromotion:
    def test_a_passing_file_is_promoted_and_extracted(self, repo, monkeypatch,
                                                      no_extract):
        stage = repo / "state" / "staging" / "SYN.NZ"

        def fake(ticker, dest, after_year, **kw):
            stage_good(dest, "SYN.NZ_Annual_FY2024.pdf")
            return 0

        monkeypatch.setattr(fetch_filings.nzx, "fetch", fake)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        assert fetch_filings.main(["SYN.NZ"]) == 0
        assert (repo / "research" / "SYN.NZ" / "PDFs"
                / "SYN.NZ_Annual_FY2024.pdf").exists()
        assert not list(stage.glob("*.pdf"))
        assert no_extract == [("SYN.NZ_Annual_FY2024.pdf", "SYN.NZ_Annual_FY2024.txt")]

    def test_extraction_is_skipped_when_the_text_already_exists(self, repo, monkeypatch,
                                                                no_extract):
        ext = repo / "research" / "SYN.NZ" / "Extracted"
        ext.mkdir(parents=True)
        (ext / "SYN.NZ_Annual_FY2024.txt").write_text("already extracted")
        monkeypatch.setattr(fetch_filings.nzx, "fetch",
                            lambda ticker, dest, after_year, **kw: (
                                stage_good(dest, "SYN.NZ_Annual_FY2024.pdf"), 0)[1])
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        fetch_filings.main(["SYN.NZ"])
        assert no_extract == []

    def test_a_malformed_label_is_normalized_before_the_gate(self, repo, monkeypatch,
                                                             no_extract):
        monkeypatch.setattr(fetch_filings.nzx, "fetch",
                            lambda ticker, dest, after_year, **kw: (
                                stage_good(dest, "SYN.NZ_Annual_FY24.pdf"), 0)[1])
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        fetch_filings.main(["SYN.NZ"])
        assert (repo / "research" / "SYN.NZ" / "PDFs"
                / "SYN.NZ_Annual_FY2024.pdf").exists()

    def test_a_failing_file_is_quarantined_with_a_reason(self, repo, monkeypatch,
                                                         no_extract, capsys):
        def fake(ticker, dest, after_year, **kw):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "SYN.NZ_Annual_FY2024.pdf").write_bytes(b"<html>403</html>" * 4000)
            return 0

        monkeypatch.setattr(fetch_filings.nzx, "fetch", fake)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        assert fetch_filings.main(["SYN.NZ"]) == 0
        q = repo / "state" / "staging" / "quarantine" / "SYN.NZ"
        assert (q / "SYN.NZ_Annual_FY2024.pdf").exists()
        assert not (repo / "research" / "SYN.NZ" / "PDFs"
                    / "SYN.NZ_Annual_FY2024.pdf").exists()
        assert "not-a-pdf" in capsys.readouterr().out

    def test_verdicts_are_logged_as_jsonl(self, repo, monkeypatch, no_extract):
        monkeypatch.setattr(fetch_filings.nzx, "fetch",
                            lambda ticker, dest, after_year, **kw: (
                                stage_good(dest, "SYN.NZ_Annual_FY2024.pdf"), 0)[1])
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        fetch_filings.main(["SYN.NZ"])
        log = repo / "state" / "staging" / "logs" / "SYN.NZ.jsonl"
        rows = [json.loads(ln) for ln in log.read_text().strip().split("\n")]
        assert rows[0]["verdict"] == "promoted"

    def test_the_summary_counts_every_stage(self, repo, monkeypatch, no_extract, capsys):
        def fake(ticker, dest, after_year, **kw):
            stage_good(dest, "SYN.NZ_Annual_FY2024.pdf")
            (dest / "SYN.NZ_Annual_FY2023.pdf").write_bytes(b"junk" * 9000)
            return 0

        monkeypatch.setattr(fetch_filings.nzx, "fetch", fake)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        fetch_filings.main(["SYN.NZ"])
        out = capsys.readouterr().out
        assert "downloaded 2" in out
        assert "promoted 1" in out
        assert "quarantined 1" in out


class TestDryRun:
    def test_the_plan_is_printed_and_nothing_is_downloaded(self, repo, monkeypatch,
                                                           capsys):
        monkeypatch.setattr(fetch_filings.nzx, "fetch",
                            lambda *a, **k: pytest.fail("dry run must not download"))
        monkeypatch.setattr(fetch_filings.nzx, "plan", lambda ticker, after_year, **kw: {
            ("Annual", "FY2026"): "440001",
            ("HalfYear", "H1-2026"): "440003",
        })
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        assert fetch_filings.main(["SYN.NZ", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "SYN.NZ_Annual_FY2026.pdf" in out
        assert "SYN.NZ_HalfYear_H1-2026.pdf" in out

    def test_nothing_is_promoted_on_a_dry_run(self, repo, monkeypatch):
        monkeypatch.setattr(fetch_filings.nzx, "plan", lambda ticker, after_year, **kw: {})
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        fetch_filings.main(["SYN.NZ", "--dry-run"])
        assert not (repo / "research" / "SYN.NZ" / "PDFs").exists()


class TestExitCodes:
    def test_a_clean_run_with_nothing_new_exits_zero(self, repo, monkeypatch, no_extract):
        monkeypatch.setattr(fetch_filings.nzx, "fetch", lambda *a, **k: 0)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        assert fetch_filings.main(["SYN.NZ"]) == 0

    def test_an_adapter_failure_exits_one_so_the_agent_takes_over(self, repo, monkeypatch,
                                                                  no_extract, capsys):
        def boom(*a, **k):
            raise RuntimeError("announcements page unreachable")

        monkeypatch.setattr(fetch_filings.nzx, "fetch", boom)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        assert fetch_filings.main(["SYN.NZ"]) == 1
        assert "unreachable" in capsys.readouterr().out

    def test_a_deadline_exits_three(self, repo, monkeypatch, no_extract, capsys):
        def slow(*a, **k):
            raise TimeoutError("deadline of 600s exceeded")

        monkeypatch.setattr(fetch_filings.nzx, "fetch", slow)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        assert fetch_filings.main(["SYN.NZ"]) == 3
        assert "deadline" in capsys.readouterr().out

    def test_files_staged_before_a_deadline_are_still_gated(self, repo, monkeypatch,
                                                            no_extract):
        """A timeout mid-run must not throw away what already downloaded."""
        def slow(ticker, dest, after_year, **kw):
            stage_good(dest, "SYN.NZ_Annual_FY2024.pdf")
            raise TimeoutError("deadline of 600s exceeded")

        monkeypatch.setattr(fetch_filings.nzx, "fetch", slow)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        assert fetch_filings.main(["SYN.NZ"]) == 3
        assert (repo / "research" / "SYN.NZ" / "PDFs"
                / "SYN.NZ_Annual_FY2024.pdf").exists()


class TestDeadlineArgument:
    def test_the_deadline_is_passed_through_to_the_adapter(self, repo, monkeypatch,
                                                           no_extract):
        seen = {}
        monkeypatch.setattr(fetch_filings.nzx, "fetch",
                            lambda ticker, dest, after_year, **kw: seen.update(kw) or 0)
        monkeypatch.setattr(fetch_filings, "holdings_count", lambda t: 9)
        fetch_filings.main(["SYN.NZ", "--deadline", "45"])
        assert seen["deadline_s"] == 45
