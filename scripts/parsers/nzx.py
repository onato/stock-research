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
from collections.abc import Iterator
from typing import Any, ClassVar

from . import common
from .base import BaseParser


class NZXParser(BaseParser):
    SUFFIXES = ("NZ",)

    # "values rounded to thousands ($000)", "All amounts are in thousands",
    # "expressed in millions". \s crosses the line breaks pdftotext leaves
    # mid-sentence.
    DECL_RE = re.compile(
        r"(?:rounded to(?:\s+the nearest)?|amounts?\s+(?:are\s+)?in|expressed in)"
        r"\s+(thousands?|millions?|billions?)", re.IGNORECASE)

    # Column-header form, curly apostrophe included.
    UNITS_COL_RE = re.compile(r"(?:NZ|A|US)?\$\s?(M\b|['’]?000\b)")

    # Inline units cell between label and numbers.
    UNIT_CELL_RE = re.compile(r"^(?:NZ)?\$\s?(m|M|['’]?000)$")

    STATED_CCY: ClassVar[list[tuple["re.Pattern[str]", str]]] = [
        (re.compile(r"(?:presented|expressed)\W{0,20}in\s+"
                    r"(?:thousands of |millions of )?new zealand dollars?", re.IGNORECASE), "NZD"),
        (re.compile(r"(?:presented|expressed)\W{0,20}in\s+"
                    r"(?:thousands of |millions of )?australian dollars?", re.IGNORECASE), "AUD"),
        (re.compile(r"(?:presented|expressed)\W{0,20}in\s+"
                    r"(?:thousands of |millions of )?(?:u\.?s\.?|united states) dollars?",
                    re.IGNORECASE), "USD"),
    ]

    # Headline figures stated in prose ("$127.2 million IFRS net profit
    # after tax" — SUM.NZ interims carry no statement tables at all). An
    # amount qualifies only when a magnitude word makes its scale explicit,
    # so the hint is per-fact and can never leak into file-level units.
    PROSE_AMT_RE = re.compile(
        r"(NZ|AU?|US)?\$\s?(\d[\d,]*(?:\.\d+)?)\s+(million|billion|thousand)s?\b",
        re.IGNORECASE)
    PROSE_SYM_CCY: ClassVar[dict[str, str]] = {
        "NZ": "NZD", "A": "AUD", "AU": "AUD", "US": "USD"}
    # Prose phrasings of the headline metrics, matched within a few words of
    # the amount. Unanchored (unlike common.PATTERNS, which anchor on the
    # statement-label start). "(?<!underlying )" keeps SUM.NZ's own KPI out
    # of the canonical NetIncome slot.
    PROSE_METRICS: ClassVar[list[tuple[str, "re.Pattern[str]"]]] = [
        ("NetIncome", re.compile(
            r"(?<!underlying )\b(?:net )?(?:profit|loss|earnings) after tax"
            r"|\bnet (?:profit|loss|income|earnings)\b", re.IGNORECASE)),
        ("EBITDA", re.compile(r"\bebitda\b", re.IGNORECASE)),
        ("Revenue", re.compile(
            r"\b(?:total |operating |group )?revenues?\b|\bnet sales\b",
            re.IGNORECASE)),
        ("OperatingCashFlow", re.compile(
            r"\b(?:net )?(?:operating )?cash ?flows? from operat", re.IGNORECASE)),
    ]
    PROSE_WINDOW = 60   # chars searched either side of the amount

    def scan(self, text: str, filename: str,
             fy_end_month: int | None = None) -> Iterator[dict[str, Any]]:
        self._lines: list[str] = common.split_lines(text)
        yield from super().scan(text, filename, fy_end_month)
        yield from self._prose_facts(filename)

    def _prose_facts(self, filename: str) -> Iterator[dict[str, Any]]:
        period = common.period_from_filename(filename)
        file_ccy = self.currency(self._lines)
        for i, line in enumerate(self._lines):
            # Amount and phrase straddle pdftotext's mid-sentence breaks, so
            # search a 3-line window but only own matches that start on line
            # i — line i+1's window will own the rest.
            window = " ".join(" ".join(self._lines[i:i + 3]).split())
            own = len(" ".join(line.split()))
            for m in self.PROSE_AMT_RE.finditer(window):
                if m.start() >= own:
                    continue
                val = common.parse_num(m.group(2))
                if val is None:
                    continue
                after = window[m.end():m.end() + self.PROSE_WINDOW]
                before = window[max(0, m.start() - self.PROSE_WINDOW):m.start()]
                metric = next(
                    (name for name, rx in self.PROSE_METRICS
                     if rx.search(after) or rx.search(before)), None)
                if metric is None:
                    continue
                sym = (m.group(1) or "").upper()
                yield {
                    "metric": metric,
                    "period": period,
                    "value_raw": val,
                    "units_hint": m.group(3).lower() + "s",
                    "source_file": filename,
                    "line_no": i + 1,
                    "context": window[:600],
                    "confidence": "prose",
                    "currency": self.PROSE_SYM_CCY.get(sym, file_ccy),
                }

    def units_hint(self, lines: list[str]) -> str | None:
        m = self.DECL_RE.search("\n".join(lines))
        if m:
            u = m.group(1).lower()
            return ("thousands" if u.startswith("thousand")
                    else "millions" if u.startswith("million") else "billions")
        return super().units_hint(lines)

    def units_for_line(self, i: int, default: str | None) -> str | None:
        m = self.UNIT_CELL_RE.search(self._lines[i].strip())
        if m is None:
            for cell in re.split(r"\s{2,}", self._lines[i].strip()):
                m = self.UNIT_CELL_RE.match(cell)
                if m:
                    break
        if m:
            return "millions" if m.group(1).lower() == "m" else "thousands"
        return default

    def currency(self, lines: list[str]) -> str | None:
        text = "\n".join(lines)
        for rx, ccy in self.STATED_CCY:
            if rx.search(text):
                return ccy
        return super().currency(lines)

    def is_note_cell(self, cell: str) -> bool:
        # A units cell sits where a note reference would; consume it the
        # same way so the numbers after it are captured.
        return bool(self.UNIT_CELL_RE.match(cell)) or super().is_note_cell(cell)
