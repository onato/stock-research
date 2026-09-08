"""NZX announcement adapter (scripts/adapters/nzx.py).

nzx.com is Next.js: the announcements page hydrates the current year's
announcements in __NEXT_DATA__ and back-years come from the per-year listing
API keyed by a CompanyID that is NOT always the ticker code (AFC.NZ is
IRG000000). The plan is a pure function of the announcement items, so the
whole selection is testable without a network call.
"""

import json
from pathlib import Path

import pytest
from adapters import nzx

FIX = Path(__file__).parent / "fixtures" / "nzx"


@pytest.fixture
def next_data():
    return json.loads((FIX / "next_data_announcements.json").read_text())


@pytest.fixture
def items(next_data):
    return nzx.items_from_hydration(next_data)


class TestHydration:
    def test_company_id_comes_from_the_announcements_query_key(self, next_data):
        assert nzx.company_id_from_hydration(next_data) == "SMI000000"

    def test_no_announcements_query_means_no_company_id(self):
        assert nzx.company_id_from_hydration({"props": {}}) is None

    def test_items_are_collected_from_the_announcements_query(self, items):
        assert [a["id"] for a in items] == [
            "440001", "440002", "440003", "440004", "440005", "440006"]

    def test_empty_hydration_yields_no_items(self):
        assert nzx.items_from_hydration({}) == []


class TestPlan:
    def test_report_types_map_to_repo_kinds(self, items):
        plan = nzx.plan_from_items(items, held=set(), after_year=0, ticker="SMI.NZ")
        assert ("Annual", "FY2026") in plan
        assert ("HalfYear", "H1-2026") in plan
        assert ("Annual", "FY2024") in plan

    def test_fllyr_counts_as_annual(self, items):
        """Small NZX companies never lodge a glossy ANNREP; the FLLYR results
        release carries the financial statements and IS the annual filing."""
        plan = nzx.plan_from_items(items, held=set(), after_year=0, ticker="SMI.NZ")
        assert plan[("Annual", "FY2023")] == "440006"

    def test_the_first_announcement_for_a_period_wins(self, items):
        """ANNREP 2026 is listed before the FLLYR for the same year; the
        listing is date-sorted so the first (newest) posting is kept."""
        plan = nzx.plan_from_items(items, held=set(), after_year=0, ticker="SMI.NZ")
        assert plan[("Annual", "FY2026")] == "440001"

    def test_non_report_types_are_ignored(self, items):
        plan = nzx.plan_from_items(items, held=set(), after_year=0, ticker="SMI.NZ")
        assert "440005" not in plan.values()   # Notice of Annual Meeting

    def test_after_year_filters_out_older_periods(self, items):
        plan = nzx.plan_from_items(items, held=set(), after_year=2025, ticker="SMI.NZ")
        assert set(plan) == {("Annual", "FY2026"), ("HalfYear", "H1-2026")}

    def test_held_periods_are_skipped(self, items):
        held = {"SMI.NZ_Annual_FY2026", "SMI.NZ_HalfYear_H1-2026"}
        plan = nzx.plan_from_items(items, held=held, after_year=0, ticker="SMI.NZ")
        assert set(plan) == {("Annual", "FY2024"), ("Annual", "FY2023")}

    def test_a_period_with_no_year_falls_back_to_the_release_date(self):
        items = [{"id": "1", "type": "ANNREP", "title": "Annual Report",
                  "releaseDate": 1725000000}]
        plan = nzx.plan_from_items(items, held=set(), after_year=0, ticker="SMI.NZ")
        assert list(plan) == [("Annual", "FY2024")]

    def test_an_fy_style_two_digit_title_year_is_expanded(self):
        items = [{"id": "9", "type": "HALFYR", "title": "Interim Report FY25",
                  "releaseDate": 0}]
        plan = nzx.plan_from_items(items, held=set(), after_year=0, ticker="SMI.NZ")
        assert list(plan) == [("HalfYear", "H1-2025")]


class TestHeld:
    def test_held_reads_both_pdfs_and_extracted(self, tmp_path):
        t = tmp_path / "research" / "SMI.NZ"
        (t / "PDFs").mkdir(parents=True)
        (t / "Extracted").mkdir()
        (t / "PDFs" / "SMI.NZ_Annual_FY2024.pdf").write_bytes(b"%PDF-")
        (t / "Extracted" / "SMI.NZ_HalfYear_H1-2025.txt").write_text("x")
        (t / "PDFs" / "OTHER.NZ_Annual_FY2024.pdf").write_bytes(b"%PDF-")
        assert nzx.held_stems("SMI.NZ", tmp_path) == {
            "SMI.NZ_Annual_FY2024", "SMI.NZ_HalfYear_H1-2025"}

    def test_missing_directories_are_empty_not_an_error(self, tmp_path):
        assert nzx.held_stems("SMI.NZ", tmp_path) == set()


class TestFetch:
    """fetch() drives the network through injectable module-level callables."""

    def test_the_fattest_attachment_wins(self, tmp_path, monkeypatch, next_data):
        """ASX cover-letter lesson: an announcement with several PDFs must
        yield the largest, not the first."""
        detail = {"pdfs": [
            "https://api.nzx.com/public/announcement/1/cover.pdf",
            "https://api.nzx.com/public/announcement/1/report.pdf",
        ]}
        sizes = {"https://api.nzx.com/public/announcement/1/cover.pdf": 2_000,
                 "https://api.nzx.com/public/announcement/1/report.pdf": 900_000}
        calls = []

        monkeypatch.setattr(nzx, "next_data", lambda url: (
            next_data if "companies" in url else detail))
        monkeypatch.setattr(nzx, "head_size", lambda url: sizes[url])
        monkeypatch.setattr(nzx, "download", lambda url, out, deadline: (
            calls.append(url), out.write_bytes(b"%PDF-fake"))[1])

        nzx.fetch("SMI.NZ", tmp_path, after_year=2025, repo=tmp_path)
        assert calls
        assert set(calls) == {"https://api.nzx.com/public/announcement/1/report.pdf"}

    def test_a_deadline_in_the_past_raises_timeout(self, tmp_path, monkeypatch, next_data):
        monkeypatch.setattr(nzx, "next_data", lambda url: next_data)
        monkeypatch.setattr(nzx, "head_size", lambda url: 1)
        monkeypatch.setattr(nzx, "download", lambda url, out, deadline: None)
        with pytest.raises(TimeoutError):
            nzx.fetch("SMI.NZ", tmp_path, after_year=2025, repo=tmp_path, deadline_s=0)
