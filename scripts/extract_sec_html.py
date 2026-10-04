#!/usr/bin/env python3
"""Table-aware text extraction for SEC iXBRL/HTML filings (20-F, 6-K).

Plain BeautifulSoup get_text() puts one table cell per line, destroying the
horizontal column alignment that scripts/parsers/ depends on (CELL_SPLIT
requires label+numbers on the same line, same shape pdftotext -layout
produces for PDF-sourced exchanges). This walks each <table>, renders its
<tr> rows as single lines with cell texts joined by multiple spaces, then
replaces the table in the soup with that rendered block before calling
get_text() on the rest. See memory note
beautifulsoup-table-flattening-bug.md (hit on AU, AngloGold Ashanti, 2026-10-03).

A third layout has zero <table> tags at all: each page is a
<div id="PageN"> of absolutely-positioned divs (financial-printer "fixed
layout" iXBRL). There is no table structure to walk, so those pages are
reconstructed by coordinates instead -- see sec-fixed-layout-ixbrl-pages.md.

Usage: python3 scripts/extract_sec_html.py INPUT.htm OUTPUT.txt
"""
import re
import sys

from bs4 import BeautifulSoup, NavigableString
from bs4.element import Tag

_TOP_RE = re.compile(r"top\s*:\s*([\d.]+)px")
_LEFT_RE = re.compile(r"left\s*:\s*([\d.]+)px")
_TOP_TOLERANCE = 2.0  # px; fragments within this band share a line


def render_table(table: Tag) -> str:
    lines = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        texts = [c.get_text(" ", strip=True) for c in cells]
        texts = [t for t in texts if t]
        if texts:
            lines.append("   ".join(texts))
    return "\n".join(lines)


def render_fixed_layout_page(page: Tag) -> str:
    """Reconstruct reading-order lines from a <div id="PageN"> of
    absolutely-positioned fragments. Coordinates are only unique within one
    page, so this must be called once per page, never on the whole document.
    """
    fragments = []
    for div in page.find_all("div", style=True):
        style = str(div["style"])
        top_m = _TOP_RE.search(style)
        left_m = _LEFT_RE.search(style)
        if not top_m or not left_m:
            continue
        # Only leaf-ish fragments: skip divs whose text comes solely from a
        # nested positioned div (that div will be visited on its own) or that
        # carry no text at all (the hairline-rule divs used for underlines).
        text = div.get_text(" ", strip=True)
        text = text.replace("\xa0", " ").strip()
        if not text:
            continue
        nested_positioned = div.find("div", style=_TOP_RE)
        if nested_positioned is not None:
            continue
        fragments.append((float(top_m.group(1)), float(left_m.group(1)), text))

    if not fragments:
        return ""

    fragments.sort(key=lambda f: (f[0], f[1]))

    rows: list[list[tuple[float, str]]] = []
    current_top = None
    for top, left, text in fragments:
        if current_top is None or abs(top - current_top) > _TOP_TOLERANCE:
            rows.append([])
            current_top = top
        rows[-1].append((left, text))

    lines = []
    for row in rows:
        row.sort(key=lambda f: f[0])
        lines.append("   ".join(text for _, text in row))
    return "\n".join(lines)


def extract(input_path: str, output_path: str) -> None:
    with open(input_path, "rb") as f:
        raw = f.read()
    soup = BeautifulSoup(raw, "html.parser")

    for table in soup.find_all("table"):
        rendered = render_table(table)
        table.replace_with(NavigableString("\n" + rendered + "\n"))

    for page in soup.find_all("div", id=re.compile(r"^Page\d+$")):
        rendered = render_fixed_layout_page(page)
        page.replace_with(NavigableString("\n" + rendered + "\n"))

    text = soup.get_text("\n")
    # Collapse excessive blank lines while preserving table row breaks.
    lines = [ln.rstrip() for ln in text.splitlines()]
    out_lines = []
    blank_run = 0
    for ln in lines:
        if ln.strip() == "":
            blank_run += 1
            if blank_run <= 1:
                out_lines.append("")
        else:
            blank_run = 0
            out_lines.append(ln)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    extract(sys.argv[1], sys.argv[2])
