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

    def test_statement_subcaption_inside_a_summary_block_is_summary(self, text):
        # 0001.HK's Ten Year Summary repeats "CONSOLIDATED INCOME STATEMENT" as
        # a sub-heading; its first column is 2015, not the filing's year.
        starts = {s.start: s for s in sections.index_text(text)}
        assert starts[33].kind == "summary"
        assert starts[34].kind == "summary"
        assert starts[37].kind == "summary"

    def test_year_row_above_the_subcaption_also_counts(self):
        # 0001.HK prints the 2015..2024 header once, above the first sub-heading.
        text = ("Ten Year Summary\n\n        2015  2016  2017  2018\n\n"
                "CONSOLIDATED INCOME STATEMENT\nHK$ million\nRevenue 1 2 3 4\n"
                "CONSOLIDATED STATEMENT OF FINANCIAL POSITION\nTotal assets 5 6 7 8\n")
        kinds = [s.kind for s in sections.index_text(text)]
        assert kinds == ["summary", "summary", "summary"]

    def test_form_feed_does_not_shift_line_numbers(self):
        text = "\fCONSOLIDATED INCOME STATEMENT\nRevenue 1 2\n\fx\n"
        assert sections.index_text(text)[0].start == 1
        assert sections.index_text(text)[0].end == 3

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


class TestPageNavColumn:
    # 0006.HK prints the report's section nav in a right-hand column on
    # every page, so a caption line reads "Consolidated Statement of Profit
    # or Loss        Financial Statements". Classify on the first cell.
    def test_caption_with_nav_column_is_a_statement(self):
        text = ("Consolidated Statement of Profit or Loss"
                "                                        Financial Statements\n"
                "For the year ended 31 December 2020\nRevenue   1,270   1,348\n")
        assert sections.index_text(text)[0].kind == "statement"

    def test_statement_caption_amid_a_year_row_is_a_summary_table(self):
        # 0004.HK ten-year table: the page header is invisible to the index,
        # so nothing marks the block as a summary except its five-year row.
        text = ("186   THE WHARF (HOLDINGS) LIMITED Annual Report 2024\n"
                "          2019      2018      2017      2016      2015\n"
                "  Year ended 31 December   HK$ Million  HK$ Million\n"
                "  Consolidated Income Statement\n"
                "  Revenue      16,874    21,055    43,273    46,627    40,875\n")
        assert sections.index_text(text)[0].kind == "summary"

    def test_two_year_statement_header_stays_a_statement(self):
        text = ("Consolidated Income Statement\n"
                "For The Year Ended 31 December 2024\n"
                "                    2024        2023\n"
                "Revenue           12,115      18,950\n")
        assert sections.index_text(text)[0].kind == "statement"

    def test_prose_about_five_years_is_not_a_summary_caption(self):
        for line in ("five years of the current regulatory period, and achieved",
                     "The summary of five-year financial results of the Group is"):
            assert sections.classify(line) is None, line

    def test_wrapped_five_year_caption_is_a_summary(self):
        text = ("Five-Year Group Profit Summary and            Financial Statements\n"
                "Group Statement of Financial Position\n"
                "Five-Year Group Profit Summary\n"
                "HK$ million        2020   2019   2018   2017   2016\n"
                "Revenue            1,270  1,348  1,555  1,420  1,288\n")
        kinds = [s.kind for s in sections.index_text(text)]
        assert kinds[0] == "summary"
        assert kinds[1] == "summary"      # sub-caption inside the summary block


class TestStatementFamily:
    def test_families_from_captions(self):
        f = sections.family
        assert f("Consolidated Income Statement") == "income"
        assert f("Consolidated Statement of Profit or Loss and Other Comprehensive Income") == "income"
        assert f("CONSOLIDATED STATEMENT OF FINANCIAL POSITION") == "position"
        assert f("Consolidated Balance Sheet") == "position"
        assert f("Consolidated Statement of Cash Flows") == "cashflow"
        assert f("Consolidated Statement of Changes in Equity") == "equity"
        assert f("Notes to the Financial Statements") is None

    def test_find_returns_the_section(self, text):
        secs = sections.index_text(text)
        assert sections.find(secs, 18).caption == "CONSOLIDATED STATEMENT OF FINANCIAL POSITION"
        assert sections.find(secs, 1) is None
