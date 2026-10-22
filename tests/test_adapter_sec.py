"""SEC EDGAR filing-text adapter (scripts/adapters/sec.py).

US filers take the XBRL route for their financial statements, but XBRL
does not carry operating KPIs -- DAUq, ARPU, subscribers, nights booked
live only in the 10-K/10-Q text. Without this adapter no US ticker had
filing text on file, and 302 of 303 XBRL-only runs since 2026-09-15 stored
no business KPI at all (RDDT's dashboard had no DAU or ARPU chart).

The plan is a pure function of EDGAR's submissions JSON, the fiscal
year-end it declares, and what is already held.
"""

import json
from pathlib import Path

import pytest
from adapters import sec

FIX = Path(__file__).parent / "fixtures" / "sec"


@pytest.fixture
def rddt():
    return json.loads((FIX / "submissions_rddt.json").read_text())


@pytest.fixture
def june():
    return json.loads((FIX / "submissions_june_fye.json").read_text())


class TestFiscalPeriod:
    @pytest.mark.parametrize(("report_date", "fye", "form", "label"), [
        ("2025-12-31", 12, "10-K", "FY2025"),
        ("2026-03-31", 12, "10-Q", "Q1-2026"),
        ("2026-06-30", 12, "10-Q", "Q2-2026"),
        ("2025-09-30", 12, "10-Q", "Q3-2025"),
        # June year-end: the quarter ending March is fiscal Q3 of the year
        # that closes the following June.
        ("2026-03-31", 6, "10-Q", "Q3-2026"),
        ("2025-09-30", 6, "10-Q", "Q1-2026"),
        ("2025-12-31", 6, "10-Q", "Q2-2026"),
        ("2025-06-30", 6, "10-K", "FY2025"),
    ])
    def test_labels(self, report_date, fye, form, label):
        assert sec.fiscal_label(report_date, fye, form) == label

    def test_a_52_53_week_period_ending_days_into_the_next_month_keeps_its_quarter(self):
        # 2025-03-29 is the close of a 13-week quarter, and so is 2025-04-02:
        # neither may be read as the month it happens to land in.
        assert sec.fiscal_label("2025-03-29", 6, "10-Q") == "Q3-2025"
        assert sec.fiscal_label("2025-04-02", 6, "10-Q") == "Q3-2025"
        assert sec.fiscal_label("2026-01-03", 12, "10-K") == "FY2025"

    def test_the_year_end_month_comes_from_the_submissions_document(self, rddt, june):
        assert sec.fye_month(rddt) == 12
        assert sec.fye_month(june) == 6


class TestPlan:
    def test_annuals_and_quarterlies_become_named_text_files(self, rddt):
        names = [p.name for p in sec.plan(rddt, "RDDT", held=set())]
        assert "RDDT_Annual_FY2025.txt" in names
        assert "RDDT_Quarterly_Q2-2026.txt" in names
        assert "RDDT_Quarterly_Q1-2026.txt" in names
        assert "RDDT_Quarterly_Q3-2025.txt" in names

    def test_only_original_10k_and_10q_filings_are_planned(self, rddt):
        plan = sec.plan(rddt, "RDDT", held=set())
        assert {p.form for p in plan} == {"10-K", "10-Q"}
        # The 10-Q/A for Q3-2025 must not replace or duplicate the original.
        q3 = [p for p in plan if p.name == "RDDT_Quarterly_Q3-2025.txt"]
        assert len(q3) == 1
        assert q3[0].url.endswith("rddt-20250930.htm")

    def test_urls_point_at_the_primary_document_in_the_archive(self, rddt):
        plan = {p.name: p for p in sec.plan(rddt, "RDDT", held=set())}
        assert plan["RDDT_Annual_FY2025.txt"].url == (
            "https://www.sec.gov/Archives/edgar/data/1713445/"
            "000171344526000022/rddt-20251231.htm")

    def test_held_filings_are_skipped(self, rddt):
        names = [p.name for p in sec.plan(
            rddt, "RDDT", held={"RDDT_Annual_FY2025", "RDDT_Quarterly_Q2-2026"})]
        assert "RDDT_Annual_FY2025.txt" not in names
        assert "RDDT_Quarterly_Q2-2026.txt" not in names
        assert "RDDT_Quarterly_Q1-2026.txt" in names

    def test_the_seed_window_is_bounded(self, rddt):
        plan = sec.plan(rddt, "RDDT", held=set(), annuals=1, quarterlies=2)
        assert [p.name for p in plan] == [
            "RDDT_Quarterly_Q2-2026.txt", "RDDT_Quarterly_Q1-2026.txt",
            "RDDT_Annual_FY2025.txt"]

    def test_a_june_year_end_filer_gets_fiscal_labels(self, june):
        names = [p.name for p in sec.plan(june, "SYNJ", held=set())]
        assert names == ["SYNJ_Quarterly_Q3-2026.txt", "SYNJ_Quarterly_Q2-2026.txt",
                         "SYNJ_Annual_FY2025.txt", "SYNJ_Quarterly_Q3-2025.txt"]


class TestFetch:
    def test_each_planned_filing_is_downloaded_and_extracted_to_text(
            self, rddt, tmp_path, monkeypatch):
        got: list[str] = []
        html = ("<html><body><p>Key Metrics</p><table><tr><td>DAUq</td>"
                "<td>130.3</td></tr></table>" + "x" * 5000 + "</body></html>")

        def fake_download(url, out, deadline=None):
            got.append(url)
            Path(out).write_text(html)

        monkeypatch.setattr(sec, "download", fake_download)
        written = sec.fetch(rddt, "RDDT", tmp_path, held=set(),
                            annuals=1, quarterlies=1)
        assert [p.name for p in written] == ["RDDT_Quarterly_Q2-2026.txt",
                                             "RDDT_Annual_FY2025.txt"]
        text = (tmp_path / "RDDT_Quarterly_Q2-2026.txt").read_text()
        assert "DAUq   130.3" in text           # table row kept on one line
        assert len(got) == 2
        assert not list(tmp_path.glob("*.htm"))  # raw HTML is not left behind

    def test_a_near_empty_document_is_not_written(self, rddt, tmp_path, monkeypatch):
        monkeypatch.setattr(sec, "download",
                            lambda url, out, deadline=None: Path(out).write_text(
                                "<html><body>Access denied</body></html>"))
        written = sec.fetch(rddt, "RDDT", tmp_path, held=set(),
                            annuals=1, quarterlies=0)
        assert written == []
        assert not list(tmp_path.iterdir())

    def test_a_spent_deadline_stops_before_the_next_download(
            self, rddt, tmp_path, monkeypatch):
        monkeypatch.setattr(sec, "download",
                            lambda *a, **k: pytest.fail("downloaded past the deadline"))
        with pytest.raises(TimeoutError):
            sec.fetch(rddt, "RDDT", tmp_path, held=set(), deadline_s=0)
