"""Deterministic ASX filing downloads (scripts/fetch_asx.py).

The ir-scraper agent spent 31 turns on TPW.AX brute-forcing ASX document
ids, hitting 403s and the Wayback Machine. The ASX site itself is
deterministic: a per-year listing page names every announcement with its
idsId, an interstitial "agree" page carries the real PDF URL in a hidden
field, and that URL serves the PDF to a plain GET. Fixtures are trimmed
copies of those two pages; no test touches the network.
"""

import datetime as dt
from pathlib import Path

import fetch_asx
import pytest

FIX = Path(__file__).parent / "fixtures" / "asx"


@pytest.fixture
def listing():
    return (FIX / "listing_2025.html").read_text()


class TestParseListing:
    def test_rows_carry_date_id_title_and_pages(self, listing):
        rows = fetch_asx.parse_listing(listing)
        assert len(rows) == 5
        r = rows[1]
        assert r.date == dt.date(2025, 8, 14)
        assert r.ids_id == "02980133"
        assert r.title == "Appendix 4E & Financial Report"   # entity decoded
        assert r.pages == 84                                  # across the line break
        assert r.price_sensitive is True
        assert rows[0].price_sensitive is False

    def test_listing_url(self):
        assert fetch_asx.listing_url("TPW", 2025) == (
            "https://www.asx.com.au/asx/v2/statistics/announcements.do"
            "?by=asxCode&asxCode=TPW&timeframe=Y&year=2025")


class TestClassify:
    @pytest.mark.parametrize(("title", "kind"), [
        ("Appendix 4E & Financial Report", "Annual"),
        ("Annual Report to shareholders", "Annual"),
        ("Appendix 4E and Annual Report", "Annual"),
        ("Half Yearly Report and Accounts", "HalfYear"),
        ("Appendix 4D & 2026 Half Year Financial Statements", "HalfYear"),
        ("Interim Financial Report", "HalfYear"),
        ("Half Year Results & Trading Update", None),     # press release
        ("Full Year Presentation", None),
        ("Notice of Annual General Meeting/Proxy Form", None),
        ("Appendix 4G", None),
        ("Appendix 3Y", None),
    ])
    def test_titles(self, title, kind):
        assert fetch_asx.classify(title) == kind


class TestFiscalLabel:
    """June year-end: FY2025 results land in Aug-2025; the half to Dec-2025
    is H1 of fiscal 2026 (the period-shift class of worksheet error)."""

    def test_annual_after_june_year_end(self):
        assert fetch_asx.fiscal_label(dt.date(2025, 8, 14), "Annual", 6) == "FY2025"

    def test_annual_for_december_year_end_reported_in_february(self):
        assert fetch_asx.fiscal_label(dt.date(2026, 2, 20), "Annual", 12) == "FY2025"

    def test_half_year_to_december_is_h1_of_next_fiscal_year(self):
        assert fetch_asx.fiscal_label(dt.date(2026, 2, 13), "HalfYear", 6) == "H1-FY2026"

    def test_half_year_to_june_for_december_year_end(self):
        assert fetch_asx.fiscal_label(dt.date(2025, 8, 20), "HalfYear", 12) == "H1-FY2025"

    def test_filename(self):
        assert fetch_asx.filename("TPW.AX", "FY2025", "Annual") == "TPW.AX_Annual_FY2025.pdf"
        assert fetch_asx.filename("TPW.AX", "H1-FY2026", "HalfYear") == "TPW.AX_HalfYear_H1-FY2026.pdf"


class TestSelect:
    def test_one_document_per_label_preferring_the_longest(self, listing):
        rows = fetch_asx.parse_listing(listing)
        picks = fetch_asx.select(rows, fy_end_month=6)
        assert {p.label: p.row.ids_id for p in picks} == {
            "FY2025": "02980133",       # the 4E, not the presentation or AGM notice
            "H1-FY2025": "02912378",    # 28-page accounts, not the 3-page update
        }

    def test_existing_files_are_skipped(self, listing, tmp_path):
        (tmp_path / "TPW.AX_Annual_FY2025.pdf").write_bytes(b"%PDF")
        rows = fetch_asx.parse_listing(listing)
        picks = fetch_asx.select(rows, fy_end_month=6)
        todo = fetch_asx.missing(picks, "TPW.AX", tmp_path)
        assert [p.label for p in todo] == ["H1-FY2025"]


class TestInterstitial:
    def test_pdf_url_from_hidden_field(self):
        html = (FIX / "interstitial.html").read_text()
        assert fetch_asx.pdf_url(html) == (
            "https://announcements.asx.com.au/asxpdf/20250814/pdf/06k1abcd2efgh3.pdf")

    def test_missing_field_is_none(self):
        assert fetch_asx.pdf_url("<html>blocked</html>") is None


class TestCli:
    def test_years_argument(self):
        assert fetch_asx.parse_years("2019-2021") == [2019, 2020, 2021]
        assert fetch_asx.parse_years("2025") == [2025]
