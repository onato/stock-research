"""LSE (.L) filings.

Glossy annual reports put marketing amounts ("saved our customers c.£1.5
billion") in the cover copy — inside the head window — while the statements
declare their scale as a £m / £'000 column header thousands of lines later.
The column-header form outranks head prose here; WISE.L read as 'billions'
without this.
"""

import re

from .base import BaseParser


class LSEParser(BaseParser):
    SUFFIXES = ("L",)

    POUND_COL_RE = re.compile(r"£\s?(m|M|['’]?000)\b")

    def units_hint(self, lines):
        for line in lines:
            m = self.POUND_COL_RE.search(line)
            if m:
                return "millions" if m.group(1).lower() == "m" else "thousands"
        return super().units_hint(lines)
