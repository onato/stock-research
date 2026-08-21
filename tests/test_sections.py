"""Section index over extracted filings.

Where a number sits in a filing matters as much as what it says: the same
label appears in the five-year summary (a different year's figure), in the
primary statement, and in a note. The index gives adjudicate.py that
context and gives the agent a line range instead of a grep.
"""

import sys
from pathlib import Path

import pytest
import sections

FIXTURE = Path(__file__).parent / "fixtures" / "extracted" / "generic" / "sections_sample.txt"


@pytest.fixture
def text():
    return FIXTURE.read_text()


class TestIndexText:
    def test_statement_sections_span_to_next_caption(self, text):
        secs = sections.index_text(text)
        starts = {s.start: s for s in secs}
        fp = starts[15]
        assert (fp.kind, fp.end) == ("statement", 21)
        assert fp.caption == "CONSOLIDATED STATEMENT OF FINANCIAL POSITION"
        assert (starts[22].kind, starts[22].end) == ("statement", 26)

    def test_summary_and_notes_are_classified(self, text):
        starts = {s.start: s for s in sections.index_text(text)}
        assert (starts[9].kind, starts[9].end) == ("summary", 14)
        assert starts[27].kind == "notes"
        assert starts[31].kind == "notes"          # "4. SEGMENT INFORMATION"

    def test_prose_mentioning_a_statement_is_not_a_caption(self, text):
        assert 29 not in {s.start for s in sections.index_text(text)}

    def test_section_of(self, text):
        secs = sections.index_text(text)
        assert sections.section_of(secs, 12) == "summary"
        assert sections.section_of(secs, 18) == "statement"
        assert sections.section_of(secs, 32) == "notes"
        assert sections.section_of(secs, 1) == "other"

    def test_pointers_skip_contents_entries(self, text):
        # The table of contents repeats every caption with a page number;
        # those one-line "sections" must not be offered as places to read.
        assert sections.pointers(sections.index_text(text)) == [
            ("CONSOLIDATED STATEMENT OF FINANCIAL POSITION", 15, 21),
            ("CONSOLIDATED STATEMENT OF COMPREHENSIVE INCOME", 22, 26)]


class TestTicker:
    def test_index_ticker_keys_by_filename(self, make_ticker, text):
        d = make_ticker("SYN.NZ")
        (d / "Extracted" / "SYN.NZ_Annual_FY2020.txt").write_text(text)
        (d / "Extracted" / "SYN.NZ_Annual_FY2019.txt").write_text("nothing here\n")
        idx = sections.index_ticker("SYN.NZ", d.parents[1])
        assert set(idx) == {"SYN.NZ_Annual_FY2020.txt", "SYN.NZ_Annual_FY2019.txt"}
        assert [s.start for s in idx["SYN.NZ_Annual_FY2020.txt"] if s.kind == "statement"] \
            == [5, 6, 15, 22]
        assert idx["SYN.NZ_Annual_FY2019.txt"] == []

    def test_cli_prints_ranges(self, make_ticker, text, monkeypatch, capsys):
        d = make_ticker("SYN.NZ")
        (d / "Extracted" / "SYN.NZ_Annual_FY2020.txt").write_text(text)
        monkeypatch.setattr(sections, "REPO", d.parents[1])
        monkeypatch.setattr(sys, "argv", ["sections.py", "SYN.NZ"])
        assert sections.main() == 0
        out = capsys.readouterr().out
        assert "SYN.NZ_Annual_FY2020.txt:15-21  statement  CONSOLIDATED STATEMENT OF FINANCIAL POSITION" in out
        assert ":9-14  summary" in out

    def test_cli_without_extracted_dir(self, make_ticker, monkeypatch, capsys):
        d = make_ticker("SYN.NZ")
        monkeypatch.setattr(sections, "REPO", d.parents[1])
        monkeypatch.setattr(sys, "argv", ["sections.py", "NOPE.NZ"])
        assert sections.main() == 2
        assert "no Extracted" in capsys.readouterr().err
