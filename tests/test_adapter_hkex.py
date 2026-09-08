"""HKEXnews filing adapter (scripts/adapters/hkex.py).

HKEXnews lists annual (t2code 40100) and interim (40200) reports under one
category that also carries ESG reports, printed-version reissues and
summaries; quarterlies live outside the category entirely and come from a
title search. The plan is a pure function of those three listings.
"""

import json
from pathlib import Path

import pytest
from adapters import hkex

FIX = Path(__file__).parent / "fixtures" / "hkex"


@pytest.fixture
def annuals():
    return json.loads((FIX / "annual_listing.json").read_text())


@pytest.fixture
def interims():
    return json.loads((FIX / "interim_listing.json").read_text())


@pytest.fixture
def quarterlies():
    return json.loads((FIX / "quarterly_listing.json").read_text())


class TestTitleYear:
    @pytest.mark.parametrize(("title", "year"), [
        ("Annual Report 2024", 2024),
        ("2024/25 Annual Report", 2025),
        ("Annual Report 2024/25", 2025),
        ("Annual Report 2024-25", 2025),
        ("Interim Report 2026", 2026),
        ("Third Quarterly Report 2025", 2025),
        ("Annual Report", 0),
    ])
    def test_years(self, title, year):
        assert hkex.title_year(title) == year

    def test_an_en_dash_split_year_is_read_as_the_later_year(self):
        assert hkex.title_year("Annual Report 2024–25") == 2025


class TestPlan:
    def test_annual_and_interim_rows_become_named_files(self, annuals, interims):
        plan = hkex.plan_from_filings(annuals, interims, [], after_year=2024,
                                      ticker="0001.HK")
        names = [p[0] for p in plan]
        assert "0001.HK_Annual_FY2025.pdf" in names
        assert "0001.HK_HalfYear_H1-2026.pdf" in names
        assert "0001.HK_HalfYear_H1-2025.pdf" in names

    def test_urls_are_absolute(self, annuals, interims):
        plan = hkex.plan_from_filings(annuals, interims, [], after_year=2024,
                                      ticker="0001.HK")
        assert all(url.startswith("https://www1.hkexnews.hk/") for _, url, _ in plan)

    def test_esg_printed_and_summary_rows_are_dropped(self, annuals, interims):
        plan = hkex.plan_from_filings(annuals, interims, [], after_year=0,
                                      ticker="0001.HK")
        urls = [url for _, url, _ in plan]
        assert not any("2024040100003" in u for u in urls)   # printed version
        assert not any("2024040100005" in u for u in urls)   # ESG report
        assert not any("2023091500004" in u for u in urls)   # summary of interim

    def test_the_first_posting_for_a_period_wins(self, annuals, interims):
        """Rows arrive date-sorted; the newest posting for a period is kept
        and the reissue that follows it is dropped."""
        plan = hkex.plan_from_filings(annuals, interims, [], after_year=0,
                                      ticker="0001.HK")
        fy2025 = [url for name, url, _ in plan if name.endswith("_Annual_FY2025.pdf")]
        assert len(fy2025) == 1
        assert "2025040100001" in fy2025[0]

    def test_after_year_filters_out_older_reports(self, annuals, interims):
        plan = hkex.plan_from_filings(annuals, interims, [], after_year=2025,
                                      ticker="0001.HK")
        assert {p[0] for p in plan} == {"0001.HK_HalfYear_H1-2026.pdf"}

    def test_quarterlies_are_named_by_their_ordinal(self, quarterlies):
        plan = hkex.plan_from_filings([], [], quarterlies, after_year=0,
                                      ticker="0700.HK")
        names = {p[0] for p in plan}
        assert names == {"0700.HK_Quarterly_Q3-2025.pdf",
                         "0700.HK_Quarterly_Q1-2025.pdf",
                         "0700.HK_Quarterly_Q3-2024.pdf"}

    def test_non_quarterly_rows_in_the_title_search_are_ignored(self, quarterlies):
        plan = hkex.plan_from_filings([], [], quarterlies, after_year=0,
                                      ticker="0700.HK")
        assert not any("Monthly" in p[0] for p in plan)

    def test_seed_mode_caps_volume(self, annuals, interims, quarterlies, monkeypatch):
        monkeypatch.setattr(hkex, "SEED_ANNUALS", 2)
        monkeypatch.setattr(hkex, "SEED_INTERIMS", 1)
        monkeypatch.setattr(hkex, "SEED_QUARTERLIES", 1)
        plan = hkex.plan_from_filings(annuals, interims, quarterlies, after_year=0,
                                      ticker="0001.HK")
        kinds = [p[0].split("_")[1] for p in plan]
        assert kinds.count("Annual") == 2
        assert kinds.count("HalfYear") == 1
        assert kinds.count("Quarterly") == 1

    def test_an_update_run_is_not_capped(self, annuals, interims, monkeypatch):
        monkeypatch.setattr(hkex, "SEED_ANNUALS", 1)
        plan = hkex.plan_from_filings(annuals, interims, [], after_year=2022,
                                      ticker="0001.HK")
        kinds = [p[0].split("_")[1] for p in plan]
        assert kinds.count("Annual") > 1

    def test_a_row_with_no_readable_year_is_dropped(self):
        plan = hkex.plan_from_filings(
            [{"TITLE": "Annual Report", "FILE_LINK": "/x.pdf"}], [], [],
            after_year=0, ticker="0001.HK")
        assert plan == []


class TestFetch:
    def test_a_deadline_in_the_past_raises_timeout(self, tmp_path, monkeypatch,
                                                   annuals, interims):
        monkeypatch.setattr(hkex, "stock_id", lambda code: 1234)
        monkeypatch.setattr(hkex, "filings",
                            lambda sid, t2: annuals if t2 == "40100" else interims)
        monkeypatch.setattr(hkex, "quarterly_filings", lambda sid: [])
        monkeypatch.setattr(hkex, "download", lambda url, out, deadline: None)
        with pytest.raises(TimeoutError):
            hkex.fetch("0001.HK", tmp_path, after_year=2024, deadline_s=0)

    def test_downloads_go_to_dest_under_the_planned_names(self, tmp_path, monkeypatch,
                                                          annuals, interims):
        monkeypatch.setattr(hkex, "stock_id", lambda code: 1234)
        monkeypatch.setattr(hkex, "filings",
                            lambda sid, t2: annuals if t2 == "40100" else interims)
        monkeypatch.setattr(hkex, "quarterly_filings", lambda sid: [])
        monkeypatch.setattr(hkex, "download",
                            lambda url, out, deadline: out.write_bytes(b"%PDF-fake"))
        hkex.fetch("0001.HK", tmp_path, after_year=2025)
        assert [p.name for p in tmp_path.glob("*.pdf")] == ["0001.HK_HalfYear_H1-2026.pdf"]
