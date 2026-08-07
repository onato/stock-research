"""Euronext (.AS) filings.

Units arrive as a statement-header declaration — "(in thousands of euro)" —
repeated at each statement and first appearing well below the 80-line head
window (FLOW.AS: line ~139), so the head-scan default saw nothing. Units can
also change mid-file (whole-euro remuneration tables between thousands
statements), so each fact takes the nearest declaration above its line
rather than one file-level value.
"""

import re
from collections.abc import Iterator
from typing import Any

from .base import BaseParser


class EuronextParser(BaseParser):
    SUFFIXES = ("AS",)

    DECL_RE = re.compile(r"in (thousands|millions|billions) of euros?\b", re.I)

    def scan(self, text: str, filename: str) -> Iterator[dict[str, Any]]:
        lines = text.splitlines()
        self._nearest: list[str | None] = []
        current: str | None = None
        for line in lines:
            m = self.DECL_RE.search(line)
            if m:
                current = m.group(1).lower()
            self._nearest.append(current)
        yield from super().scan(text, filename)

    def units_hint(self, lines: list[str]) -> str | None:
        for line in lines:
            m = self.DECL_RE.search(line)
            if m:
                return m.group(1).lower()
        return super().units_hint(lines)

    def units_for_line(self, i: int, default: str | None) -> str | None:
        return self._nearest[i] or default

    def currency(self, lines: list[str]) -> str | None:
        if any(self.DECL_RE.search(line) for line in lines):
            return "EUR"
        return super().currency(lines)
