#!/usr/bin/env python3
"""Deterministic metric extraction: text filings -> DuckDB facts table.

Replaces the grep/read loop that dominated cost. Measured on AFC.NZ, the
financial-parser subagent spent 183 turns and 18.2M cache-read tokens
re-reading 18 filings (644k tokens of source) 3-6 times each, repeating
one grep pattern 17 times -- a 28x amplification.

This does that search ONCE, linearly, with no model. The agent then
queries the result and spends its turns on judgment instead of hunting.

DELIBERATE NON-GOALS. This script must never:
  * scale units (a wrong thousands/millions call is a 1000x error)
  * pick between competing candidates
  * infer a period the filename does not state
Every candidate is emitted with context; adjudication is the agent's job.
That division is the whole point -- financial-parser.md's rules (the DUOL
SBC double-count, authorization-vs-actual buybacks) encode judgment that
regexes cannot replicate.

Usage: build_facts.py TICKER [--show]
"""

import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import schema  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]

# Metric -> regexes matched against the line label. Sourced from the search
# strings already documented in .claude/agents/financial-parser.md.
PATTERNS = {
    # "(?!\s*tax)" because HLG.NZ's "Total income tax expense" line was
    # tagged as Revenue, poisoning the whole candidate set for the agent.
    "Revenue": [r"^(total |operating |net )?revenue", r"^net sales",
                r"^total income(?!\s*tax)"],
    "CostOfRevenue": [r"^cost of (sales|revenue|goods sold)"],
    "GrossProfit": [r"^gross (profit|margin|loss)"],
    "OperatingIncome": [r"^operating (profit|loss|income)", r"^ebit\b",
                        r"^(profit|loss) from operations"],
    "EBITDA": [r"\bebitda\b"],   # adjusted/underlying/normalised all qualify
    "NetIncome": [r"^(net )?(profit|loss)( after tax| for the (year|period))",
                  r"^net (income|earnings)", r"^(profit|loss) attributable to"],
    "ProfitBeforeTax": [r"^(profit|loss) before (income )?tax"],
    # "net loss per share, basic", "basic and diluted (cents per share)",
    # "total basic earnings per share" -- per-share is the anchor.
    "EPS": [r"(earnings|loss|income) per (ordinary |stapled )?share",
            r"^(basic|diluted)( and diluted)?\b.{0,30}per share",
            r"^(basic|diluted) eps", r"per share.{0,15}(basic|diluted)"],
    # Filers write this a dozen ways: "net cash flows from", "net cash
    # (used in)/provided by", "cash generated from operations", "net cash
    # inflow/(outflow) from". Match on the operating-activities anchor and
    # allow any connector, rather than enumerating phrasings.
    "OperatingCashFlow": [r"^(net )?cash.{0,40}operating activities",
                          r"^cash generated (from|by) operation",
                          r"^cash flows? from operating"],
    "CapEx": [r"^(purchase|acquisition|additions?) of (property|plant|equipment|fixed)",
              r"^capital expenditure"],
    "Depreciation": [r"^depreciation( and amortisation| charge)?",
                     r"^amortisation( and impairment)?"],
    "ShareholdersEquity": [r"^total (shareholders.? )?equity", r"^net assets",
                           r"^equity attributable to"],
    "TotalAssets": [r"^total assets"],
    "TotalLiabilities": [r"^total liabilities"],
    "CashAndEquivalents": [r"^cash and (cash )?equivalents", r"^cash at bank"],
    "TotalDebt": [r"^(total )?borrowings", r"^(total )?(interest.bearing )?debt",
                  r"^loans and borrowings"],
    "SharesOutstanding": [r"^(weighted average )?(number of )?(ordinary )?shares",
                          r"shares on issue"],
    "StockBasedComp": [r"^(stock|share).based (compensation|payment)",
                       r"^share.based payment"],
    # Never matched on any of 64 tickers with the old wording. US filers
    # say "taxes paid related to net share settlement"; others use
    # "tax withholding on" or "shares withheld for taxes".
    "EquityAwardTaxes": [r"tax(es)?.{0,40}(net.share settlement|equity award)",
                         r"(tax withholding|withheld for tax).{0,30}(share|equity|rsu)",
                         r"shares withheld.{0,20}tax"],
    "InterestIncome": [r"^(interest|investment|finance) income"],
    "InterestExpense": [r"^(interest|finance) (expense|costs?)"],
    # "share buybacks", "purchase of treasury shares", "repurchases of
    # ordinary shares" -- anchor on the act, not the sentence shape.
    "ShareRepurchases": [r"repurchase|buy.?back", r"treasury (stock|shares)"],
    # Exclude "interest and dividends" (an income line) and dividend
    # reinvestment plans, which are not cash returned to shareholders.
    "DividendsPaid": [r"^dividends?( paid| declared| to equity)",
                      r"^(payment of |paid )dividends?"],
    "DeferredRevenue": [r"^(deferred|unearned) (revenue|income)",
                        r"^contract liabilit"],
    "CashTaxesPaid": [r"^(net |corporate |corporation )?(income )?tax(es|ation)? paid",
                      r"^tax(es)? (paid|refunded)"],
}
COMPILED = {m: [re.compile(p, re.I) for p in pats] for m, pats in PATTERNS.items()}

# Statement lines are matched by splitting on runs of 2+ spaces and
# looking for a label cell followed by numeric cells. The old approach --
# one regex anchored at column 0 and end-of-line -- silently rejected two
# common pdftotext layouts before the metric patterns ever ran:
#   * indented statements: every AIR.NZ line starts with a leading space,
#     which produced 0 candidates across 22 filings;
#   * interleaved pages: two report pages rendered side by side, so lines
#     read "Cargo  487  459  Other comprehensive (loss)/income:" and the
#     trailing second-column text broke the $ anchor (HLG.NZ, WISE.L).
# Cell segmentation handles both: each column's label+numbers becomes its
# own segment, and text after the numbers just ends that segment.
NUM = r"\(\s?-?[\d,]+\.?\d*\s?\)|-?[\d,]+\.?\d*|—|–|-"
CELL_SPLIT = re.compile(r"\s{2,}")
NUM_CELL = re.compile(r"^(?:" + NUM + r")(?:\s+(?:" + NUM + r"))*$")
# Label cap raised 60 -> 80: real statement lines get long. "Payments
# for taxes related to net share settlement of equity awards" is 67
# characters. 80 still excludes prose, which runs well past it.
LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ,.&/()'’\-]{2,80}$")
# Note-reference column between label and numbers: "C5", "C1, C5",
# "5.1, 5.2", "Note 16", "2 (b)", "iii". Requires a digit, a "Note"/roman
# marker, or a dotted list so ordinary short words and genuine value
# columns are not skipped as notes. Only honored when numbers follow.
_REF = r"[A-Za-z]{1,3}\d{1,2}|\d{1,2}(?:\.\d{1,2})+"
NOTE_CELL = re.compile(r"^(?:" + _REF + r")(?:,\s*(?:" + _REF + r"))+$"
                       r"|^[A-Za-z]{1,3}\d{1,2}$"
                       r"|^Notes?\s+\d{1,2}$"
                       r"|^\d{1,2}\s?\([a-z]\)$"
                       r"|^[ivx]{1,4}$", re.I)
UNITS_RE = re.compile(
    r"\b(?:in|expressed in|amounts in)?\s*"
    r"(thousands?|millions?|billions?|000s?|\$000|NZ\$000)\b", re.I)
# NZX filers declare units as a column header ("$M", "NZ$M", "$'000")
# rather than a sentence; scanned when the sentence form finds nothing.
UNITS_COL_RE = re.compile(r"(?:NZ|A|US)?\$\s?(M\b|'?000\b)")
CURRENCY_RE = re.compile(r"\b(NZ\$|AU\$|US\$|NZD|AUD|USD|GBP|EUR|HKD|SGD|CAD)\b")


def parse_num(tok):
    tok = tok.strip()
    if tok in ("-", "—", "–", ""):
        return 0.0
    neg = tok.startswith("(") and tok.endswith(")")
    tok = tok.strip("()").replace(",", "")
    try:
        v = float(tok)
    except ValueError:
        return None
    return -v if neg else v


def segments(line):
    """Yield (label, numbers) pairs from one line.

    Cells are separated by 2+ spaces. A segment is a label cell followed
    by one or more all-numeric cells; anything else ends the segment, so
    a second interleaved column starts a fresh one. Prose collapses into
    a single non-numeric cell and yields nothing.
    """
    cells = CELL_SPLIT.split(line.strip())
    i = 0
    while i < len(cells):
        if LABEL_RE.match(cells[i]):
            nums = []
            j = i + 1
            if j < len(cells) and NOTE_CELL.match(cells[j]) \
                    and j + 1 < len(cells) and NUM_CELL.match(cells[j + 1]):
                j += 1
            while j < len(cells) and NUM_CELL.match(cells[j]):
                nums.extend(parse_num(t) for t in re.findall(NUM, cells[j]))
                j += 1
            nums = [n for n in nums if n is not None]
            if nums:
                yield cells[i], nums
                i = j
                continue
        i += 1


def period_from_filename(name):
    """Filenames follow {TICKER}_{type}_{period}.txt (see CLAUDE.md)."""
    m = re.search(r"_(FY\d{4})", name, re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"_(H[12])[-_ ]?(\d{4})", name, re.I)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    m = re.search(r"_(Q[1-4])[-_ ]?(\d{4})", name, re.I)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"
    return None


def scan_file(path):
    """Yield candidate facts from one extracted filing."""
    lines = path.read_text(errors="replace").splitlines()
    period = period_from_filename(path.name)

    # Units/currency hints: scan the header region and remember the last
    # declaration seen above each match, since statements often restate it.
    units_hint = None
    currency = None
    head = "\n".join(lines[:80])
    um = UNITS_RE.search(head)
    if um:
        u = um.group(1).lower()
        units_hint = ("thousands" if u.startswith(("thousand", "000", "$000", "nz$000"))
                      else "millions" if u.startswith("million")
                      else "billions" if u.startswith("billion") else None)
    cm = CURRENCY_RE.search(head)
    if cm:
        currency = cm.group(1).replace("$", "D").upper() if "$" in cm.group(1) else cm.group(1).upper()
    if units_hint is None:
        # Column-header form ("$M", "$'000") -- first declaration wins.
        for line in lines:
            um = UNITS_COL_RE.search(line)
            if um:
                units_hint = "millions" if um.group(1).startswith("M") else "thousands"
                break

    for i, line in enumerate(lines):
        for label, nums in segments(line):
            # A leading small integer is usually a note reference, not a value.
            if len(nums) > 1 and nums[0] == int(nums[0]) and 0 < nums[0] < 100:
                nums = nums[1:]
            if not nums:
                continue

            for metric, regexes in COMPILED.items():
                if not any(r.search(label) for r in regexes):
                    continue
                ctx = "\n".join(lines[max(0, i - 2):i + 3])
                # First column is the reporting period; a second is the prior
                # comparative. Emit both -- the prior year cross-checks the
                # value already extracted from that year's own filing.
                for col, val in enumerate(nums[:2]):
                    yield {
                        "metric": metric,
                        "period": period if col == 0 else None,
                        "value_raw": val,
                        "units_hint": units_hint,
                        "source_file": path.name,
                        "line_no": i + 1,
                        "context": ctx[:600],
                        "confidence": "statement_line" if col == 0 else "prior_year_column",
                        "currency": currency,
                    }
                break


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("usage: build_facts.py TICKER [--show]", file=sys.stderr)
        return 2
    ticker = args[0]

    extracted = REPO / "research" / ticker / "Extracted"
    if not extracted.is_dir():
        print(f"no Extracted/ for {ticker} -- run pdftotext first", file=sys.stderr)
        return 1

    files = sorted(extracted.glob("*.txt"))
    facts = []
    for f in files:
        facts.extend(scan_file(f))

    import duckdb
    db = REPO / "research" / ticker / "Reports" / f"{ticker}.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    con.execute(schema.create_sql())
    # Migration shim: schema.py now declares currency in the facts DDL, but
    # DBs created before that lack the column. Remove once every ticker DB
    # has been rebuilt at least once.
    try:
        con.execute("ALTER TABLE facts ADD COLUMN currency TEXT")
    except Exception:
        pass
    con.execute("DELETE FROM facts")
    if facts:
        con.executemany(
            "INSERT INTO facts (metric, period, value_raw, units_hint, source_file,"
            " line_no, context, confidence, currency)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [[f[k] for k in ("metric", "period", "value_raw", "units_hint",
                             "source_file", "line_no", "context", "confidence",
                             "currency")] for f in facts],
        )
    summary = con.execute(
        "SELECT metric, count(*), count(DISTINCT period) FROM facts"
        " GROUP BY metric ORDER BY 2 DESC").fetchall()
    hints = con.execute(
        "SELECT DISTINCT units_hint FROM facts WHERE units_hint IS NOT NULL").fetchall()
    con.close()

    print(f"{ticker}: {len(facts)} candidates from {len(files)} filings -> {db.name}")
    print(f"  units hint: {[h[0] for h in hints] or 'NONE FOUND (agent must determine)'}")
    if "--show" in sys.argv:
        print(f"\n  {'metric':22s} {'rows':>5s} {'periods':>8s}")
        for metric, n, p in summary:
            print(f"    {metric:20s} {n:5d} {p:8d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
