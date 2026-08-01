"""Euronext (.AS) filings.

Units arrive as a statement-header declaration — "(in thousands of euro)" —
repeated at each statement and first appearing well below the 80-line head
window (FLOW.AS: line ~139), so the head-scan default saw nothing. Units can
also change mid-file (whole-euro remuneration tables between thousands
statements), so each fact takes the nearest declaration above its line
rather than one file-level value.
"""

import re

from .base import BaseParser


class EuronextParser(BaseParser):
    SUFFIXES = ("AS",)

    DECL_RE = re.compile(r"in (thousands|millions|billions) of euros?\b", re.I)

    def scan(self, text, filename):
        lines = text.splitlines()
        self._nearest = []
        current = None
        for line in lines:
            m = self.DECL_RE.search(line)
            if m:
                current = m.group(1).lower()
            self._nearest.append(current)
        yield from super().scan(text, filename)

    def units_hint(self, lines):
        for line in lines:
            m = self.DECL_RE.search(line)
            if m:
                return m.group(1).lower()
        return super().units_hint(lines)

    def units_for_line(self, i, default):
        return self._nearest[i] or default

    def currency(self, lines):
        if any(self.DECL_RE.search(line) for line in lines):
            return "EUR"
        return super().currency(lines)
