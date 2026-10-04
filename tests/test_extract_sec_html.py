"""extract_sec_html renders <table> rows as single aligned lines.

Plain BeautifulSoup get_text() puts one table cell per line, which breaks
the label+numbers-on-one-line shape scripts/parsers/ requires (the same
shape pdftotext -layout produces). See memory note
beautifulsoup-table-flattening-bug.md.
"""

from pathlib import Path

from extract_sec_html import extract

FIXTURES = Path(__file__).parent / "fixtures" / "sec_fixed_layout"


def test_table_rows_are_rendered_as_one_line_per_row(tmp_path):
    html = """
    <html><body>
    <p>Some narrative text.</p>
    <table>
      <tr><th>Revenue</th><th>FY2025</th><th>FY2024</th></tr>
      <tr><td>Revenue</td><td>2,454,877</td><td>2,398,039</td></tr>
      <tr><td>Net income</td><td>102,918</td><td>84,210</td></tr>
    </table>
    <p>More narrative text.</p>
    </body></html>
    """
    src = tmp_path / "in.htm"
    out = tmp_path / "out.txt"
    src.write_text(html)

    extract(str(src), str(out))

    lines = out.read_text().splitlines()
    assert any("Revenue" in ln and "2,454,877" in ln and "2,398,039" in ln
               for ln in lines)
    assert any("Net income" in ln and "102,918" in ln and "84,210" in ln
               for ln in lines)
    assert any("Some narrative text." in ln for ln in lines)


def test_empty_cells_are_dropped_not_padded(tmp_path):
    html = """
    <html><body>
    <table>
      <tr><td>Label</td><td></td><td>123</td></tr>
    </table>
    </body></html>
    """
    src = tmp_path / "in.htm"
    out = tmp_path / "out.txt"
    src.write_text(html)

    extract(str(src), str(out))

    text = out.read_text()
    assert "Label" in text
    assert "123" in text


def test_blank_line_runs_are_collapsed(tmp_path):
    html = "<html><body><p>A</p><p></p><p></p><p></p><p>B</p></body></html>"
    src = tmp_path / "in.htm"
    out = tmp_path / "out.txt"
    src.write_text(html)

    extract(str(src), str(out))

    lines = out.read_text().splitlines()
    # no run of more than one consecutive blank line
    blank_run = 0
    for ln in lines:
        blank_run = blank_run + 1 if ln.strip() == "" else 0
        assert blank_run <= 1


def test_fixed_layout_page_reconstructs_statement_lines(tmp_path):
    """Some filings (see sec-fixed-layout-ixbrl-pages.md) have zero <table>
    tags: each page is a <div id="PageN"> of absolutely-positioned divs with
    no table structure. Text extraction must reconstruct lines by grouping
    fragments with matching `top` into a row, then ordering by `left`,
    producing the same label+numbers-on-one-line shape a <table> or
    pdftotext -layout would.
    """
    src = FIXTURES / "td_fy2025_income_statement_excerpt.htm"
    out = tmp_path / "out.txt"

    extract(str(src), str(out))

    lines = out.read_text().splitlines()

    assert any(
        "Total revenue" in ln and "67,777" in ln and "57,223" in ln
        for ln in lines
    ), "label and both comparative-year numbers must land on one line"
    assert any(
        "Insurance revenue" in ln and "7,737" in ln and "6,952" in ln
        for ln in lines
    )
    # A fragment from Page2 must not be reconstructed onto a Page1 line just
    # because its `top` coordinate happens to collide -- coordinates reset
    # per page.
    assert not any(
        "Total revenue" in ln and "20,538" in ln for ln in lines
    )
    assert any("Net income" in ln and "20,538" in ln and "8,842" in ln for ln in lines)
