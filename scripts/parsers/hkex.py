"""HKEX (.HK) filings.

Bilingual-era annual reports (roughly FY2018 onward) render every statement
label as "English 中文" and every units header as RMB’000 with a curly
apostrophe — both invisible to the generic parser, which is why 0285.HK
FY2024 yielded a single (false-positive) fact while the English-only 2015-17
filings yielded ~100 each.

  * clean_label strips the trailing CJK so "REVENUE 收入" matches the
    vocabulary;
  * units tokens (RMB’000, HK$'000, RMB million) are recognized anywhere in
    the file — statements declare them at the column header, far below the
    80-line head window;
  * currency likewise scans the whole file (RMB -> CNY);
  * financial summaries carry several comparative year columns; the third
    is emitted as another prior_year_column instead of being discarded.
"""

import re
from collections import Counter
from collections.abc import Iterator

from .base import BaseParser


class HKEXParser(BaseParser):
    SUFFIXES = ("HK",)

    # Five-year summaries and dual-comparative statements: keep one extra
    # comparative column. Same confidence vocabulary the agent already
    # handles, so no prompt change is needed.
    MAX_VALUE_COLUMNS = 3

    # CJK unified ideographs, CJK punctuation, and fullwidth forms: the
    # Chinese rendering appended to every label, header and note.
    CJK_RE = re.compile(r"[一-鿿　-〿＀-￯].*$")

    UNITS_TOKEN_RE = re.compile(
        r"(?:RMB|HK\$|US\$|\$)\s?(?:['’]\s?)?(000\b|million|billion|m\b|bn\b)", re.IGNORECASE)

    # Table-header declarations: "(in thousands)", "(RMB, in thousands)",
    # "(All amounts in thousands, ...)", "(in millions)" — how the US-style
    # filers (NetEase 6-Ks, Bilibili 20-Fs/6-Ks, Alibaba HK annuals) declare
    # units, always at the statement, far below the head window.
    PAREN_DECL_RE = re.compile(
        r"\(\s*(?:RMB\s*,?\s*|All amounts\s+)?in (thousands|millions|billions)",
        re.IGNORECASE)

    # `HK$ 4.46` / `US$ 0.57`: a currency token glued to a per-share figure.
    # Dropping it makes the cell numeric, so the note reference before it is
    # recognised as one (0001.HK EPS read as 10, the note number, not 4.46).
    CCY_PREFIX = re.compile(r"(?:HK|US|S|A|NZ|RMB)?\$\s*(?=[\d(])")

    def segments(self, line: str) -> Iterator[tuple[str, list[float]]]:
        return super().segments(self.CCY_PREFIX.sub("", line))

    def clean_label(self, cell: str) -> str:
        return self.CJK_RE.sub("", cell).strip()

    def units_hint(self, lines: list[str]) -> str | None:
        """Majority of the units tokens in the file. Taking the first match
        let narrative ("HK$136.8 billion") outvote thirty statement headers
        reading "HK$ Million" (0004.HK half-years hinted billions)."""
        votes: Counter[str] = Counter()
        for line in lines:
            for m in self.PAREN_DECL_RE.finditer(line):
                votes[self._scale(m.group(1))] += 3   # an explicit declaration
            for m in self.UNITS_TOKEN_RE.finditer(line):
                votes[self._scale(m.group(1))] += 1
        if votes:
            return votes.most_common(1)[0][0]
        return super().units_hint(lines)

    @staticmethod
    def _scale(token: str) -> str:
        u = token.lower()
        return ("thousands" if u.startswith(("000", "thousand"))
                else "millions" if u.startswith(("million", "m")) else "billions")

    def currency(self, lines: list[str]) -> str | None:
        return self._search_currency("\n".join(lines))
