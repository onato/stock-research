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
        r"(?:RMB|HK\$|US\$)\s?(?:['’]\s?)?(000\b|million|billion)", re.I)

    # Table-header declarations: "(in thousands)", "(RMB, in thousands)",
    # "(All amounts in thousands, ...)", "(in millions)" — how the US-style
    # filers (NetEase 6-Ks, Bilibili 20-Fs/6-Ks, Alibaba HK annuals) declare
    # units, always at the statement, far below the head window.
    PAREN_DECL_RE = re.compile(
        r"\(\s*(?:RMB\s*,?\s*|All amounts\s+)?in (thousands|millions|billions)",
        re.I)

    def clean_label(self, cell):
        return self.CJK_RE.sub("", cell).strip()

    def units_hint(self, lines):
        for line in lines:
            m = self.PAREN_DECL_RE.search(line) or self.UNITS_TOKEN_RE.search(line)
            if m:
                u = m.group(1).lower()
                return ("thousands" if u.startswith(("000", "thousand"))
                        else "millions" if u.startswith("million")
                        else "billions")
        return super().units_hint(lines)

    def currency(self, lines):
        return self._search_currency("\n".join(lines))
