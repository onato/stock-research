"""The deterministic gate between a staged download and research/ (scripts/filing_gate.py).

Nothing an adapter (or the ir-scraper agent) downloads reaches research/ on its
own say-so: it must be named to the repo convention, be a real PDF, be big
enough to be a report, extract non-trivial text, mention the company, and not
overwrite something already on file. The fixture is a committed synthetic
40 KB two-page PDF that names "Synthetic Holdings Limited".
"""

import json
from pathlib import Path

import filing_gate
import pytest

FIX = Path(__file__).parent / "fixtures" / "pdf"
GOOD_PDF = FIX / "synthetic_annual.pdf"
COMPANY = "Synthetic Holdings Limited"


class TestNormalizeName:
    @pytest.mark.parametrize(("raw", "fixed"), [
        ("SMI.NZ_Annual_FY26.pdf", "SMI.NZ_Annual_FY2026.pdf"),
        ("SMI.NZ_HalfYear_H1-FY26.pdf", "SMI.NZ_HalfYear_H1-2026.pdf"),
        ("SMI.NZ_HalfYear_H1-26.pdf", "SMI.NZ_HalfYear_H1-2026.pdf"),
        ("SMI.NZ_HalfYear_1H26.pdf", "SMI.NZ_HalfYear_H1-2026.pdf"),
        ("SMI.NZ_HalfYear_2H26.pdf", "SMI.NZ_HalfYear_H2-2026.pdf"),
        ("SMI.NZ_HalfYear_HY26.pdf", "SMI.NZ_HalfYear_H1-2026.pdf"),
        ("0001.HK_Quarterly_Q3-25.pdf", "0001.HK_Quarterly_Q3-2025.pdf"),
    ])
    def test_short_labels_are_expanded(self, raw, fixed):
        assert filing_gate.normalize_name(raw) == fixed

    @pytest.mark.parametrize("name", [
        "SMI.NZ_Annual_FY2026.pdf",
        "SMI.NZ_HalfYear_H1-2026.pdf",
        "0001.HK_Quarterly_Q3-2025.pdf",
    ])
    def test_already_canonical_names_are_untouched(self, name):
        assert filing_gate.normalize_name(name) == name

    def test_normalize_is_idempotent(self):
        once = filing_gate.normalize_name("SMI.NZ_Annual_FY26.pdf")
        assert filing_gate.normalize_name(once) == once


@pytest.fixture
def staged(tmp_path):
    """A staging dir + an empty dest dir; returns (stage, dest)."""
    stage = tmp_path / "stage"
    dest = tmp_path / "dest"
    stage.mkdir()
    dest.mkdir()
    return stage, dest


def _stage(stage: Path, name: str, source: Path = GOOD_PDF) -> Path:
    out = stage / name
    out.write_bytes(source.read_bytes())
    return out


class TestCheck:
    def test_a_good_file_passes(self, staged):
        stage, dest = staged
        f = _stage(stage, "SYN.NZ_Annual_FY2024.pdf")
        assert filing_gate.check(f, "SYN.NZ", COMPANY, dest) is None

    @pytest.mark.parametrize("name", [
        "SYN.NZ_Yearly_FY2024.pdf",       # not one of the four report types
        "OTHER.NZ_Annual_FY2024.pdf",     # wrong ticker
        "SYN.NZ-Annual-FY2024.pdf",       # wrong separator
        "annual report.pdf",              # no convention at all
    ])
    def test_bad_filenames_are_rejected(self, staged, name):
        stage, dest = staged
        f = _stage(stage, name)
        assert filing_gate.check(f, "SYN.NZ", COMPANY, dest) == "bad-filename"

    def test_all_four_report_types_are_accepted(self, staged):
        stage, dest = staged
        for kind in ("Annual", "HalfYear", "Quarterly", "Presentation"):
            f = _stage(stage, f"SYN.NZ_{kind}_FY2024.pdf")
            assert filing_gate.check(f, "SYN.NZ", COMPANY, dest) is None

    def test_html_masquerading_as_a_pdf_is_rejected(self, staged):
        stage, dest = staged
        f = stage / "SYN.NZ_Annual_FY2024.pdf"
        f.write_bytes(b"<html><body>Access denied</body></html>" + b"x" * 40_000)
        assert filing_gate.check(f, "SYN.NZ", COMPANY, dest) == "not-a-pdf"

    def test_a_tiny_pdf_is_rejected(self, staged):
        stage, dest = staged
        f = stage / "SYN.NZ_Annual_FY2024.pdf"
        f.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
        assert filing_gate.check(f, "SYN.NZ", COMPANY, dest).startswith("too-small")

    def test_a_pdf_with_no_extractable_text_is_rejected(self, staged, monkeypatch):
        stage, dest = staged
        f = _stage(stage, "SYN.NZ_Annual_FY2024.pdf")
        monkeypatch.setattr(filing_gate, "pdf_text", lambda path: "   ")
        assert filing_gate.check(f, "SYN.NZ", COMPANY, dest) == "no-extractable-text"

    def test_pdftotext_failure_is_reported(self, staged, monkeypatch):
        stage, dest = staged
        f = _stage(stage, "SYN.NZ_Annual_FY2024.pdf")

        def boom(path):
            raise OSError("pdftotext not found")

        monkeypatch.setattr(filing_gate, "pdf_text", boom)
        assert filing_gate.check(f, "SYN.NZ", COMPANY, dest).startswith("pdftotext-failed")

    def test_the_wrong_company_is_rejected(self, staged):
        stage, dest = staged
        f = _stage(stage, "SYN.NZ_Annual_FY2024.pdf")
        reason = filing_gate.check(f, "SYN.NZ", "Fisher & Paykel Healthcare", dest)
        assert reason.startswith("company-name-missing")

    def test_an_unknown_company_skips_the_name_check(self, staged):
        """company == ticker means state/companies.json had no name; a
        name check against the ticker would reject every genuine filing."""
        stage, dest = staged
        f = _stage(stage, "SYN.NZ_Annual_FY2024.pdf")
        assert filing_gate.check(f, "SYN.NZ", "SYN.NZ", dest) is None
        assert filing_gate.check(f, "SYN.NZ", "", dest) is None

    def test_first_two_name_words_are_enough(self, staged):
        """The gate matches on the distinctive leading words, so a longer
        registered name than the one printed on the cover still passes."""
        stage, dest = staged
        f = _stage(stage, "SYN.NZ_Annual_FY2024.pdf")
        assert filing_gate.check(f, "SYN.NZ", "Synthetic Holdings Group Limited", dest) is None

    def test_an_existing_destination_file_is_not_overwritten(self, staged):
        stage, dest = staged
        f = _stage(stage, "SYN.NZ_Annual_FY2024.pdf")
        (dest / "SYN.NZ_Annual_FY2024.pdf").write_bytes(b"%PDF-existing")
        assert filing_gate.check(f, "SYN.NZ", COMPANY, dest) == "already-exists"


class TestCompanyName:
    def test_info_json_name_wins_over_companies_json(self, tmp_path):
        (tmp_path / "research" / "SYN.NZ").mkdir(parents=True)
        (tmp_path / "state").mkdir()
        (tmp_path / "research" / "SYN.NZ" / "info.json").write_text(
            json.dumps({"name": "Synthetic Holdings Limited"}))
        (tmp_path / "state" / "companies.json").write_text(
            json.dumps({"SYN.NZ": {"name": "Stale Old Name"}}))
        assert filing_gate.company_name("SYN.NZ", tmp_path) == "Synthetic Holdings Limited"

    def test_companies_json_is_the_fallback(self, tmp_path):
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "companies.json").write_text(
            json.dumps({"SYN.NZ": {"name": "Synthetic Holdings Limited"}}))
        assert filing_gate.company_name("SYN.NZ", tmp_path) == "Synthetic Holdings Limited"

    def test_unknown_ticker_returns_the_ticker(self, tmp_path):
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "companies.json").write_text("{}")
        assert filing_gate.company_name("SYN.NZ", tmp_path) == "SYN.NZ"

    def test_missing_files_do_not_raise(self, tmp_path):
        assert filing_gate.company_name("SYN.NZ", tmp_path) == "SYN.NZ"


class TestLogging:
    def test_each_verdict_is_one_json_line(self, tmp_path):
        log = tmp_path / "state" / "staging" / "logs" / "SYN.NZ.jsonl"
        filing_gate.log_verdicts(log, [
            {"file": "SYN.NZ_Annual_FY2024.pdf", "verdict": "promoted"},
            {"file": "SYN.NZ_Annual_FY2023.pdf", "verdict": "quarantined",
             "reason": "too-small (12 bytes)"},
        ])
        lines = log.read_text().strip().split("\n")
        assert len(lines) == 2
        rows = [json.loads(ln) for ln in lines]
        assert rows[0]["verdict"] == "promoted"
        assert rows[1]["reason"].startswith("too-small")
        assert all("ts" in r for r in rows)

    def test_logging_appends(self, tmp_path):
        log = tmp_path / "logs" / "SYN.NZ.jsonl"
        filing_gate.log_verdicts(log, [{"file": "a.pdf", "verdict": "promoted"}])
        filing_gate.log_verdicts(log, [{"file": "b.pdf", "verdict": "promoted"}])
        assert len(log.read_text().strip().split("\n")) == 2
