"""BaseParser: the scan driver plus the strategy hooks exchanges override.

The driver composes hooks exactly as the original single-file scanner did;
every default reproduces that behavior bit-for-bit, verified by the corpus
snapshot (tests/tools/corpus_snapshot.py) across all 73 tickers. BaseParser
itself is the generic fallback for exchanges without a dedicated subclass.

The candidate-emitter contract (see build_facts.py DELIBERATE NON-GOALS)
binds every subclass: emit raw candidates with hints — never scale units,
never pick between competing candidates, never infer a period the filename
does not state. A subclass may make the HINTS more accurate for its
exchange; adjudication stays the financial-parser agent's job.

Open/closed rule: an exchange's quirk goes in its subclass, never here.
Adding an exchange = new module in this package (SUFFIXES set) + a fixture
directory + a test module. No shared-code edits.
"""

import re

from . import common


class BaseParser:
    # Listing suffixes this parser owns ("NZ",) — empty = not registered,
    # which makes BaseParser itself the unregistered-suffix fallback.
    SUFFIXES = ()

    # How many numeric columns of a statement line to emit. The first is
    # the filing's own period, the rest are comparative columns.
    MAX_VALUE_COLUMNS = 2

    # How many lines of the file head are scanned for units/currency.
    HEAD_LINES = 80

    # Note-reference column between label and numbers: "C5", "C1, C5",
    # "5.1, 5.2", "Note 16", "2 (b)", "iii". Requires a digit, a
    # "Note"/roman marker, or a dotted list so ordinary short words and
    # genuine value columns are not skipped as notes.
    _REF = r"[A-Za-z]{1,3}\d{1,2}|\d{1,2}(?:\.\d{1,2})+"
    NOTE_CELL = re.compile(r"^(?:" + _REF + r")(?:,\s*(?:" + _REF + r"))+$"
                           r"|^[A-Za-z]{1,3}\d{1,2}$"
                           r"|^Notes?\s+\d{1,2}$"
                           r"|^\d{1,2}\s?\([a-z]\)$"
                           r"|^[ivx]{1,4}$", re.I)

    # Sentence-form units declaration ("amounts in thousands", "$000").
    UNITS_RE = re.compile(
        r"\b(?:in|expressed in|amounts in)?\s*"
        r"(thousands?|millions?|billions?|000s?|\$000|NZ\$000)\b", re.I)

    # Column-header units form ("$M", "$'000"); scanned over the whole file
    # when the sentence form finds nothing in the head. None disables.
    UNITS_COL_RE = re.compile(r"(?:NZ|A|US)?\$\s?(M\b|'?000\b)")

    # Symbols carry no word boundaries: "$" and "£" are non-word characters,
    # so "NZ$ thousands" can never satisfy a trailing \b (the bug that left
    # currency NULL for every non-US filing). ISO codes keep their \b.
    CURRENCY_RE = re.compile(
        r"(NZ\$|AU\$|US\$|HK\$|S\$|C\$|£|€)"
        r"|\b(NZD|AUD|USD|GBP|EUR|HKD|SGD|CAD|RMB|CNY|JPY)\b")

    SYMBOL_CCY = {"NZ$": "NZD", "AU$": "AUD", "US$": "USD", "HK$": "HKD",
                  "S$": "SGD", "C$": "CAD", "£": "GBP", "€": "EUR"}

    # ------------------------------------------------------------------
    # The driver. Hermetic: text in, fact dicts out. No I/O.
    # ------------------------------------------------------------------

    def scan(self, text, filename):
        """Yield candidate facts from one extracted filing."""
        lines = text.splitlines()
        period = common.period_from_filename(filename)
        units_hint = self.units_hint(lines)
        currency = self.currency(lines)

        for i, line in enumerate(lines):
            for label, nums in self.segments(line):
                nums = self.strip_leading_note_ref(nums)
                if not nums:
                    continue

                for metric, regexes in common.COMPILED.items():
                    if not any(r.search(label) for r in regexes):
                        continue
                    ctx = "\n".join(lines[max(0, i - 2):i + 3])
                    # First column is the reporting period; the rest are
                    # comparatives. Emit both -- a prior-year column
                    # cross-checks the value already extracted from that
                    # year's own filing.
                    for col, val in enumerate(nums[:self.MAX_VALUE_COLUMNS]):
                        yield {
                            "metric": metric,
                            "period": period if col == 0 else None,
                            "value_raw": val,
                            "units_hint": self.units_for_line(i, units_hint),
                            "source_file": filename,
                            "line_no": i + 1,
                            "context": ctx[:600],
                            "confidence": ("statement_line" if col == 0
                                           else "prior_year_column"),
                            "currency": currency,
                        }
                    break

    # ------------------------------------------------------------------
    # Strategy hooks. Defaults = the original scanner, bit-for-bit.
    # ------------------------------------------------------------------

    def units_hint(self, lines):
        """File-level units hint from the head, else the column-header form."""
        head = "\n".join(lines[:self.HEAD_LINES])
        um = self.UNITS_RE.search(head)
        if um:
            u = um.group(1).lower()
            return ("thousands" if u.startswith(("thousand", "000", "$000", "nz$000"))
                    else "millions" if u.startswith("million")
                    else "billions" if u.startswith("billion") else None)
        if self.UNITS_COL_RE is not None:
            for line in lines:
                um = self.UNITS_COL_RE.search(line)
                if um:
                    return "millions" if um.group(1).startswith("M") else "thousands"
        return None

    def units_for_line(self, i, default):
        """Per-line override point: statements often restate units locally."""
        return default

    def currency(self, lines):
        """Currency hint from the file head, normalized to an ISO code."""
        head = "\n".join(lines[:self.HEAD_LINES])
        cm = self.CURRENCY_RE.search(head)
        if not cm:
            return None
        tok = cm.group(0)
        if tok in self.SYMBOL_CCY:
            return self.SYMBOL_CCY[tok]
        return "CNY" if tok.upper() == "RMB" else tok.upper()

    def segments(self, line):
        """Yield (label, numbers) pairs from one line.

        Cells are separated by 2+ spaces. A segment is a label cell followed
        by one or more all-numeric cells; anything else ends the segment, so
        a second interleaved column starts a fresh one. Prose collapses into
        a single non-numeric cell and yields nothing.
        """
        cells = common.CELL_SPLIT.split(line.strip())
        i = 0
        while i < len(cells):
            if common.LABEL_RE.match(cells[i]):
                nums = []
                j = i + 1
                if j < len(cells) and self.is_note_cell(cells[j]) \
                        and j + 1 < len(cells) and common.NUM_CELL.match(cells[j + 1]):
                    j += 1
                while j < len(cells) and common.NUM_CELL.match(cells[j]):
                    nums.extend(common.parse_num(t)
                                for t in re.findall(common.NUM, cells[j]))
                    j += 1
                nums = [n for n in nums if n is not None]
                if nums:
                    yield cells[i], nums
                    i = j
                    continue
            i += 1

    def is_note_cell(self, cell):
        return bool(self.NOTE_CELL.match(cell))

    def strip_leading_note_ref(self, nums):
        """A leading small integer is usually a note reference, not a value."""
        if len(nums) > 1 and nums[0] == int(nums[0]) and 0 < nums[0] < 100:
            return nums[1:]
        return nums
