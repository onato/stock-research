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

MIN_POINTER_LINES = 5   # a contents entry is one line; a statement is many


@dataclass(frozen=True)
class Section:
    kind: str      # "statement" | "summary" | "notes"
    caption: str
    start: int     # 1-based, inclusive
    end: int       # 1-based, inclusive


def classify(line: str) -> tuple[str, str] | None:
    """(kind, caption) if the line is a section caption, else None."""
    text = line.strip()
    if not text or len(text) > 90:
        return None
    for kind, rx in (("statement", STATEMENT_RE), ("summary", SUMMARY_RE),
                     ("notes", NOTES_RE)):
        m = rx.match(text)
        if m and TRAILER_RE.fullmatch(text[m.end():]):
            return kind, text
    return None


def index_text(text: str) -> list[Section]:
    lines = text.splitlines()
    hits = [(i + 1, *c) for i, ln in enumerate(lines) if (c := classify(ln))]
    out: list[Section] = []
    for n, (start, kind, caption) in enumerate(hits):
        end = hits[n + 1][0] - 1 if n + 1 < len(hits) else len(lines)
        out.append(Section(kind, caption, start, end))
    return out


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
