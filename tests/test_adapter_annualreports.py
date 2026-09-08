"""AnnualReports.com archive adapter (scripts/adapters/annualreports.py).

The archive's hosted URLs are fully derivable:
  .../AnnualReportArchive/{initial}/{EXCH}_{CODE}_{YEAR}.pdf
where {initial} is the first letter of the company name. That makes the whole
candidate list a pure function of ticker, name and year range — the only
network call is the HEAD that says which of them exist.
"""

import datetime as dt

import pytest
from adapters import annualreports as ar

TODAY = dt.date(2026, 9, 8)


class TestCandidateUrls:
    def test_url_shape(self):
        candidates = ar.candidate_urls(
            "BNZL.L", "Bunzl plc", after_year=2024, today=TODAY)
        assert [y for y, _, _ in candidates] == [2026, 2025]
        year, url, name = candidates[1]
        assert year == 2025
        assert url == ("https://www.annualreports.com/HostedData/AnnualReportArchive"
                       "/b/LSE_BNZL_2025.pdf")
        assert name == "BNZL.L_Annual_FY2025.pdf"

    @pytest.mark.parametrize(("ticker", "exch"), [
        ("BNZL.L", "LSE"), ("TPW.AX", "ASX"), ("SMI.NZ", "NZE"),
    ])
    def test_suffix_maps_to_the_archive_exchange(self, ticker, exch):
        urls = ar.candidate_urls(ticker, "Acme Limited", after_year=2024, today=TODAY)
        assert all(f"/{exch}_" in u for _, u, _ in urls)

    def test_an_unmapped_suffix_yields_nothing(self):
        assert ar.candidate_urls("DUOL", "Duolingo Inc", after_year=2024,
                                 today=TODAY) == []
        assert ar.candidate_urls("0001.HK", "CK Hutchison", after_year=2024,
                                 today=TODAY) == []

    def test_the_initial_comes_from_the_first_letter_of_the_name(self):
        urls = ar.candidate_urls("XYZ.L", "3i Group plc", after_year=2024, today=TODAY)
        assert all("/i/" in u for _, u, _ in urls)   # digits are not archive letters

    def test_a_nameless_ticker_yields_nothing(self):
        assert ar.candidate_urls("XYZ.L", "", after_year=2024, today=TODAY) == []
        assert ar.candidate_urls("XYZ.L", "123", after_year=2024, today=TODAY) == []

    def test_after_year_is_the_exclusive_floor(self):
        years = [y for y, _, _ in ar.candidate_urls(
            "BNZL.L", "Bunzl plc", after_year=2022, today=TODAY)]
        assert years == [2026, 2025, 2024, 2023]

    def test_seeding_walks_back_a_fixed_window(self):
        years = [y for y, _, _ in ar.candidate_urls(
            "BNZL.L", "Bunzl plc", after_year=0, today=TODAY)]
        assert years[0] == 2026
        assert len(years) == ar.SEED_YEARS
        assert years[-1] == 2026 - ar.SEED_YEARS + 1

    def test_newest_first(self):
        years = [y for y, _, _ in ar.candidate_urls(
            "BNZL.L", "Bunzl plc", after_year=2020, today=TODAY)]
        assert years == sorted(years, reverse=True)


class TestFetch:
    def test_only_urls_that_exist_are_downloaded(self, tmp_path, monkeypatch):
        live = {2025}
        monkeypatch.setattr(ar, "head_ok", lambda url: any(
            str(y) in url for y in live))
        monkeypatch.setattr(ar, "download",
                            lambda url, out, deadline: out.write_bytes(b"%PDF-fake"))
        n = ar.fetch("BNZL.L", tmp_path, after_year=2022, company_name="Bunzl plc",
                     today=TODAY)
        assert n == 1
        assert [p.name for p in tmp_path.glob("*.pdf")] == ["BNZL.L_Annual_FY2025.pdf"]

    def test_a_deadline_in_the_past_raises_timeout(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ar, "head_ok", lambda url: True)
        monkeypatch.setattr(ar, "download", lambda url, out, deadline: None)
        with pytest.raises(TimeoutError):
            ar.fetch("BNZL.L", tmp_path, after_year=2022, company_name="Bunzl plc",
                     today=TODAY, deadline_s=0)
