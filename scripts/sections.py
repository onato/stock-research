#!/usr/bin/env python3
"""Index the sections of an extracted filing by their captions.

A filing prints the same label in several places: a five-year summary (where
the first column is often the OLDEST year), the primary statement, and the
notes. `build_facts.py` emits all of them as candidates and cannot tell them
apart -- ARG.NZ's sole FY2020 equity candidate came from the summary table
and was FY2016's figure. Knowing the section a line sits in is what lets
adjudicate.py rank a statement line above a summary or a note, and what
lets the agent open a filing at `file:start-end` instead of grepping for
the caption and paging to it (~35-45 grep+Read pairs per ticker).

Hermetic: text in, sections out. Line numbers are 1-based, inclusive, and
match `facts.line_no`.

Usage:
  sections.py TICKER      # print every section of every extracted filing
"""

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parents[1]

PREFIX = r"(?:consolidated |group |company |parent |interim |condensed )*"
STATEMENT_RE = re.compile(
    PREFIX + r"(?:statements? of (?:comprehensive income|profit or loss"
    r"(?: and other comprehensive income)?|profit and loss|financial position|"
    r"financial performance|cash ?flows?|changes in equity|movements in equity)"
    r"|income statements?|balance sheets?|cash ?flow statements?)", re.IGNORECASE)
SUMMARY_RE = re.compile(
    r"(?:\w+ ){0,2}(?:financial summary|summary|highlights|key (?:financial )?"
    r"(?:metrics|figures|statistics|numbers)|at a glance|five[- ]year|"
    r"ten[- ]year|performance summary|track record)", re.IGNORECASE)
NOTES_RE = re.compile(
    r"(?:notes to the (?:consolidated |group |interim )?financial statements"
    r"|\d{1,2}[.)]?\s+[A-Za-z][^.]{2,70})", re.IGNORECASE)
# What may follow a caption on the same line: nothing, a page number, a
# "(continued)" marker, or layout punctuation. Prose may not.
TRAILER_RE = re.compile(r"(?:\s*\((?:continued|cont\.?)\))?[\s\d:|\-–—.]*$", re.IGNORECASE)

CELL_SPLIT = re.compile(r"\s{2,}")
# A heading, not a sentence: every word capitalised bar small connectors, no
# trailing "is"/comma, at most eight words.
TITLE_RE = re.compile(
    r"(?:[A-Z][\w'’-]*|and|of|the|for|&)(?:\s+(?:[A-Z][\w'’-]*|and|of|the|for|&)){0,7}")
MIN_POINTER_LINES = 5   # a contents entry is one line; a statement is many
SUBCAPTION_WINDOW = 12  # a statement caption this soon after a summary one may be its sub-heading
CHAIN_WINDOW = 40       # once one sub-heading is found, later ones may follow a page-long block
YEAR_WINDOW = 10        # lines either side of a caption searched for its year row
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
MULTI_YEAR_TABLE = 4    # a statement shows two years (three if restated); a summary shows five+


@dataclass(frozen=True)
class Section:
    kind: str      # "statement" | "summary" | "notes"
    caption: str
    start: int     # 1-based, inclusive
    end: int       # 1-based, inclusive


def classify(line: str) -> tuple[str, str] | None:
    """(kind, caption) if the line is a section caption, else None.

    Only the first cell counts: reports print a page-navigation column on
    the right ("... Profit or Loss        Financial Statements").
    """
    text = CELL_SPLIT.split(line.strip(), maxsplit=1)[0].strip()
    if not text or len(text) > 90:
        return None
    for kind, rx in (("statement", STATEMENT_RE), ("summary", SUMMARY_RE),
                     ("notes", NOTES_RE)):
        m = rx.match(text)
        if not m:
            continue
        rest = text[m.end():]
        # A summary caption may carry more words ("Five-Year Group Profit
        # Summary and"); a statement caption may not, or prose would match.
        if TRAILER_RE.fullmatch(rest) or (kind == "summary" and TITLE_RE.fullmatch(text)):
            return kind, text
    return None


def index_text(text: str) -> list[Section]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()          # a final newline is not an extra line to sed
    hits = [(i + 1, *c) for i, ln in enumerate(lines) if (c := classify(ln))]
    out: list[Section] = []
    chained = False
    for n, (start, found, caption) in enumerate(hits):
        kind = found
        end = hits[n + 1][0] - 1 if n + 1 < len(hits) else len(lines)
        # A statement caption a few lines into a summary block, with a row of
        # three or more years just above or below it, is the summary's own
        # sub-heading
        # (0001.HK "Ten Year Summary" repeats "CONSOLIDATED INCOME STATEMENT";
        # its first column is 2015). Once one sub-heading is found, the
        # block's later ones follow without needing their own year row.
        near = lines[max(0, start - YEAR_WINDOW):start + YEAR_WINDOW]
        prev_len = out[-1].end - out[-1].start + 1 if out else 0
        after_summary = bool(out) and out[-1].kind == "summary"
        if kind == "statement" and (
                (after_summary and prev_len <= SUBCAPTION_WINDOW and _many_years(near))
                or (after_summary and chained and prev_len <= CHAIN_WINDOW)
                or _many_years(near, MULTI_YEAR_TABLE)):
            kind, chained = "summary", True
        elif kind != "statement" or not out or out[-1].kind != "summary":
            chained = False
        out.append(Section(kind, caption, start, end))
    return out


def _many_years(block: list[str], n: int = 3) -> bool:
    years = set(YEAR_RE.findall(" ".join(block)))
    return len(years) >= n


FAMILY_RE = (
    ("income", re.compile(r"income statement|profit or loss|profit and loss|comprehensive income|"
                          r"financial performance", re.IGNORECASE)),
    ("position", re.compile(r"financial position|balance sheet", re.IGNORECASE)),
    ("cashflow", re.compile(r"cash ?flow", re.IGNORECASE)),
    ("equity", re.compile(r"changes in equity|movements in equity", re.IGNORECASE)),
)


def family(caption: str) -> str | None:
    """Which primary statement a caption names, or None for anything else."""
    for name, rx in FAMILY_RE:
        if rx.search(caption):
            return name
    return None


def find(secs: list[Section], line_no: int) -> Section | None:
    for s in secs:
        if s.start <= line_no <= s.end:
            return s
    return None


def section_of(secs: list[Section], line_no: int) -> str:
    """The kind of section a line sits in; "other" before the first caption."""
    for s in secs:
        if s.start <= line_no <= s.end:
            return s.kind
    return "other"


def pointers(secs: list[Section]) -> list[tuple[str, int, int]]:
    """Primary-statement ranges worth opening, contents entries excluded."""
    return [(s.caption, s.start, s.end) for s in secs
            if s.kind == "statement" and s.end - s.start + 1 >= MIN_POINTER_LINES]


def index_ticker(ticker: str, repo: pathlib.Path = REPO) -> dict[str, list[Section]]:
    folder = repo / "research" / ticker / "Extracted"
    if not folder.is_dir():
        return {}
    return {f.name: index_text(f.read_text(errors="replace"))
            for f in sorted(folder.glob("*.txt"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    args = ap.parse_args()
    idx = index_ticker(args.ticker, REPO)
    if not idx:
        print(f"{args.ticker}: no Extracted/*.txt to index", file=sys.stderr)
        return 2
    for name, secs in idx.items():
        for s in secs:
            print(f"{name}:{s.start}-{s.end}  {s.kind:9s}  {s.caption}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
