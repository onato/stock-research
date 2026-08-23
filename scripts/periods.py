#!/usr/bin/env python3
"""Parse the period labels used across every ticker DB.

Cross-ticker screening needs to know what a period *is*: a TTM cannot be
reconstructed, and a 5-year CAGR cannot be anchored, from an opaque string.
The corpus holds 2,274 distinct labels in 13 format families -- the same six
months spelled `H1 2026`, `H1-2026`, `H1 FY2026` and `H1-FY2026`, and the same
quarter spelled `Q1 2020` (DB) or `Q1-2020` (CSV, the PYPL divergence).

Rather than accumulate one regex per family, normalisation collapses the
separator and FY-prefix spellings first, leaving exactly four shapes to match.

The year in an interim label is the FISCAL year, not the calendar year --
verified against SPK.NZ, whose `H1 2026` revenue (1917) is half of `FY2026`
(3700-ish), not of `FY2025`.

DELIBERATE NON-GOALS, mirroring build_facts.py:
  * This module never infers a period from context, and never guesses at a
    label it does not recognise. Unparseable input yields OTHER with a null
    fiscal year -- a missing row is obvious, a plausible wrong one is not.
  * It knows nothing about calendar dates. Fiscal year ends differ per ticker
    and are not encoded in these labels; anything needing a real date must
    resolve it elsewhere.
"""

import re
from dataclasses import dataclass
from typing import Literal

PeriodType = Literal["FY", "H1", "H2", "Q1", "Q2", "Q3", "Q4", "9M", "OTHER"]

# The four bespoke labels in the corpus. Listed literally because none of them
# can be inferred safely: a 15-month year and a 6-month stub look like ordinary
# years to any regex, and summing one into a TTM produces a number that is
# wrong but plausible. FY2016-Jun is the only one that IS a normal 12-month
# year -- the suffix just records which month it ends in.
IRREGULAR: dict[str, tuple[int, PeriodType, int]] = {
    "FY2016 JUN":      (2016, "FY",    12),
    "FY2017 15MO":     (2017, "OTHER", 15),
    "FY2018 6MOSTUB":  (2018, "OTHER", 6),
    "FY2021 (10MO)":   (2021, "OTHER", 10),
}

# The raw spellings, for tests and callers that want to whitelist them.
IRREGULAR_LABELS: frozenset[str] = frozenset(
    {"FY2016-Jun", "FY2017-15mo", "FY2018-6moStub", "FY2021 (10mo)"})

_ANNUAL = re.compile(r"^FY(\d{4})$")
# A bare `2024` names a year without asserting it is a full fiscal one, so it
# sorts chronologically but never anchors a CAGR. Absent from the corpus today;
# export_csv accepted it before this module existed, so it still does.
_BARE_YEAR = re.compile(r"^(\d{4})$")
_HALF = re.compile(r"^H([12]) (\d{4})$")
_QUARTER = re.compile(r"^Q([1-4]) (\d{4})$")
_MONTHS = re.compile(r"^(\d{1,2})M (\d{4})$")
# A fiscal year cut short (or stretched) by a balance-date change:
# `FY2021-7mo`, `FY2025-8mo`. NZK.NZ changed balance date twice and produces
# both. The length is inferred, but the period stays OTHER -- `is_annual`
# must keep saying False so no TTM path ever sums a 7-month year.
_STUB_YEAR = re.compile(r"^FY(\d{4}) (\d{1,2})MO$")

# A trailing 3-letter month names WHICH month-end a period ran to, for
# tickers whose balance date moved: NZK.NZ has both a 12-month year to
# 31 Jan 2025 and an 8-month stub to 30 Sep 2025, and a bare `FY2025`
# cannot tell them apart. The suffix disambiguates the period; it says
# nothing about its length, so it is stripped and the rest parses as
# normal (`FY2025-Jan` is a full year, `H1 FY2026-Jul` a normal half).
_MONTH_QUALIFIER = re.compile(
    r"[ -](JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)$")

# `FY` is dropped only when it prefixes a 4-digit year inside a multi-token
# label (`H1 FY2026` -> `H1 2026`). A bare `FY2026` keeps it: there, `FY` is
# what marks the row as a full year rather than an interim.
_INNER_FY = re.compile(r"(?<=\s)FY(?=\d{4}\b)")


@dataclass(frozen=True, slots=True)
class Period:
    """A parsed period label.

    `months` is the length of the reporting window, which is what makes a
    period comparable: only 12-month rows may anchor a CAGR, and only equal
    windows may be differenced into a TTM.
    """

    raw: str
    fiscal_year: int | None
    ptype: PeriodType
    months: int | None
    sort_key: tuple[int, int, str]


def _normalize(label: str) -> str:
    s = label.strip().upper().replace("-", " ").replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return _INNER_FY.sub("", s)


def sub_rank(ptype: PeriodType) -> int:
    """Ordering within a fiscal year; the full year sorts after its parts.

    Matches export_csv.sort_key, which every committed CSV is already
    ordered by.

    Public because `refresh_plan` compares periods by
    (fiscal_year, sub_rank) rather than by `sort_key`: sort_key ties on the
    raw uppercased label, so `Q1-2026` sorts above `Q1 2026` on separator
    alone, which is a spelling difference and not a chronological one.
    """
    if ptype == "FY":
        return 9
    if ptype in ("H1", "H2"):
        return int(ptype[1]) * 2
    if ptype.startswith("Q"):
        return int(ptype[1])
    return 0


# Retained so existing internal callers keep working.
_sub_rank = sub_rank


def parse(label: str | None) -> Period:
    """Parse a period label. Never raises; unknown input yields OTHER."""
    raw = "" if label is None else str(label)
    norm = _normalize(raw)
    # export_csv.sort_key ties on the stripped/uppercased ORIGINAL, separators
    # and all. Reusing that exact string keeps the two orderings byte-identical
    # so no committed CSV is reordered when this replaces the incumbent.
    tie = raw.strip().upper()

    if norm in IRREGULAR:
        iyear, iptype, imonths = IRREGULAR[norm]
        return Period(raw, iyear, iptype, imonths,
                      (iyear, _sub_rank(iptype), tie))

    year: int | None = None
    ptype: PeriodType = "OTHER"
    months: int | None = None

    # Strip a trailing month qualifier so the label parses on its own shape.
    # Done after the IRREGULAR lookup so a hardcoded entry always wins.
    norm = _MONTH_QUALIFIER.sub("", norm)

    if m := _ANNUAL.match(norm):
        year, ptype, months = int(m.group(1)), "FY", 12
    elif m := _STUB_YEAR.match(norm):
        # Year and length are known; ptype stays OTHER so it sorts in the
        # right place without ever counting as a comparable full year.
        year, months = int(m.group(1)), int(m.group(2))
    elif m := _HALF.match(norm):
        half = m.group(1)
        year, months = int(m.group(2)), 6
        ptype = "H1" if half == "1" else "H2"
    elif m := _QUARTER.match(norm):
        year, months = int(m.group(2)), 3
        ptype = f"Q{m.group(1)}"  # type: ignore[assignment]
    elif m := _MONTHS.match(norm):
        year, months = int(m.group(2)), int(m.group(1))
        # Only the 9-month stub is a recognised reporting window; anything
        # else keeps its length but stays OTHER so no TTM path will use it.
        ptype = "9M" if months == 9 else "OTHER"
    elif m := _BARE_YEAR.match(norm):
        # Sorts by year, but stays OTHER: nothing states this is 12 months.
        year = int(m.group(1))

    return Period(raw, year, ptype, months, (year or 0, _sub_rank(ptype), tie))


def sort_key(label: str | None) -> tuple[int, int, str]:
    """Chronological ordering, byte-compatible with export_csv.sort_key.

    Dashboards plot in row order, so 'oldest first' matters and the existing
    CSV ordering must not change.
    """
    return parse(label).sort_key


def is_annual(p: Period) -> bool:
    """True only for a genuine 12-month year -- the CAGR anchor.

    `FY2017-15mo` is FY-shaped and must not qualify.
    """
    return p.ptype == "FY" and p.months == 12


def canonical(p: Period) -> str:
    """The unambiguous spelling of a period: `FY2026`, `H1 FY2026`, `Q3 FY2026`."""
    if p.fiscal_year is None or p.ptype == "OTHER":
        return p.raw
    if p.ptype == "FY":
        return f"FY{p.fiscal_year}"
    return f"{p.ptype} FY{p.fiscal_year}"


def prior_year(p: Period) -> str | None:
    """The same period one fiscal year earlier, canonically spelled.

    Returns None when the period has no usable fiscal year, so callers
    cannot accidentally difference against a fabricated label.
    """
    if p.fiscal_year is None or p.ptype == "OTHER":
        return None
    if p.ptype == "FY":
        return f"FY{p.fiscal_year - 1}"
    return f"{p.ptype} FY{p.fiscal_year - 1}"
