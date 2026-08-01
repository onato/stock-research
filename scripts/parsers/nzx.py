"""NZX (.NZ) filings.

Three local conventions, all learned from real filings:

  * Units are declared in a rounding sentence deep in the notes ("values
    rounded to thousands ($000) unless otherwise stated" — AGL.NZ) while
    the report head is full of marketing amounts ("$267.8 million"), which
    fooled the head-prose scan into 'millions' for thousands filings. The
    declaration, wherever it appears, outranks head prose.
  * Column headers write thousands with a curly apostrophe ($’000) as
    often as a straight one ($'000).
  * Some filers (AIR.NZ) put the units in an inline cell between label and
    numbers: "Operating revenue   $m   6,755   6,752". The cell is
    consumed like a note reference and sets that line's units hint.

Currency: a stated presentation currency ("presented in New Zealand
dollars", "... Australian dollars" for AUD reporters like ANZ.NZ) outranks
stray symbols in the head.
"""

import re

from .base import BaseParser


class NZXParser(BaseParser):
    SUFFIXES = ("NZ",)

    # "values rounded to thousands ($000)", "All amounts are in thousands",
    # "expressed in millions". \s crosses the line breaks pdftotext leaves
    # mid-sentence.
    DECL_RE = re.compile(
        r"(?:rounded to(?:\s+the nearest)?|amounts?\s+(?:are\s+)?in|expressed in)"
        r"\s+(thousands?|millions?|billions?)", re.I)

    # Column-header form, curly apostrophe included.
    UNITS_COL_RE = re.compile(r"(?:NZ|A|US)?\$\s?(M\b|['’]?000\b)")

    # Inline units cell between label and numbers.
    UNIT_CELL_RE = re.compile(r"^(?:NZ)?\$\s?(m|M|['’]?000)$")

    STATED_CCY = [
        (re.compile(r"(?:presented|expressed)\W{0,20}in\s+"
                    r"(?:thousands of |millions of )?new zealand dollars?", re.I), "NZD"),
        (re.compile(r"(?:presented|expressed)\W{0,20}in\s+"
                    r"(?:thousands of |millions of )?australian dollars?", re.I), "AUD"),
        (re.compile(r"(?:presented|expressed)\W{0,20}in\s+"
                    r"(?:thousands of |millions of )?(?:u\.?s\.?|united states) dollars?",
                    re.I), "USD"),
    ]

    def scan(self, text, filename):
        self._lines = text.splitlines()
        yield from super().scan(text, filename)

    def units_hint(self, lines):
        m = self.DECL_RE.search("\n".join(lines))
        if m:
            u = m.group(1).lower()
            return ("thousands" if u.startswith("thousand")
                    else "millions" if u.startswith("million") else "billions")
        return super().units_hint(lines)

    def units_for_line(self, i, default):
        m = self.UNIT_CELL_RE.search(self._lines[i].strip())
        if m is None:
            for cell in re.split(r"\s{2,}", self._lines[i].strip()):
                m = self.UNIT_CELL_RE.match(cell)
                if m:
                    break
        if m:
            return "millions" if m.group(1).lower() == "m" else "thousands"
        return default

    def currency(self, lines):
        text = "\n".join(lines)
        for rx, ccy in self.STATED_CCY:
            if rx.search(text):
                return ccy
        return super().currency(lines)

    def is_note_cell(self, cell):
        # A units cell sits where a note reference would; consume it the
        # same way so the numbers after it are captured.
        return bool(self.UNIT_CELL_RE.match(cell)) or super().is_note_cell(cell)
