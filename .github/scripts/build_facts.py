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

REPO = pathlib.Path(__file__).resolve().parents[2]

# Metric -> regexes matched against the line label. Sourced from the search
# strings already documented in .claude/agents/financial-parser.md.
PATTERNS = {
    "Revenue": [r"^(total |operating |net )?revenue", r"^net sales", r"^total income"],
    "CostOfRevenue": [r"^cost of (sales|revenue|goods sold)"],
    "GrossProfit": [r"^gross (profit|margin|loss)"],
    "OperatingIncome": [r"^operating (profit|loss|income)", r"^ebit\b",
                        r"^(profit|loss) from operations"],
    "EBITDA": [r"^(adjusted )?ebitda", r"^underlying ebitda"],
    "NetIncome": [r"^(net )?(profit|loss)( after tax| for the (year|period))",
                  r"^net (income|earnings)", r"^(profit|loss) attributable to"],
    "ProfitBeforeTax": [r"^(profit|loss) before (income )?tax"],
    "EPS": [r"^(basic|diluted).{0,25}earnings per share",
            r"^earnings per share", r"^(basic|diluted) eps"],
    "OperatingCashFlow": [r"net cash (from|provided by|generated).{0,20}operat",
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
    "EquityAwardTaxes": [r"taxes paid.{0,30}(net.share settlement|equity awards)"],
    "InterestIncome": [r"^(interest|investment|finance) income"],
    "InterestExpense": [r"^(interest|finance) (expense|costs?)"],
    "ShareRepurchases": [r"^(repurchase|buyback|buy.back) of", r"^treasury stock"],
    "DividendsPaid": [r"^dividends? paid", r"^dividends? (declared|per share)"],
    "DeferredRevenue": [r"^(deferred|unearned) (revenue|income)",
                        r"^contract liabilit"],
    "CashTaxesPaid": [r"^(income )?tax(es)? paid"],
}
COMPILED = {m: [re.compile(p, re.I) for p in pats] for m, pats in PATTERNS.items()}

# A statement line: label, optional note ref, then 1+ numeric columns.
# Numbers may be (1,234) for negatives, or a bare '-' for nil.
NUM = r"\(?-?[\d,]+\.?\d*\)?|—|–|-"
LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9 ,.&/()'\-]{2,60}?)\s{2,}"
    r"(?P<rest>(?:\s*(?:" + NUM + r"))+)\s*$"
)
UNITS_RE = re.compile(
    r"\b(?:in|expressed in|amounts in)?\s*"
    r"(thousands?|millions?|billions?|000s?|\$000|NZ\$000)\b", re.I)
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

    for i, line in enumerate(lines):
        m = LINE_RE.match(line.rstrip())
        if not m:
            continue
        label = m.group("label").strip()
        nums = [parse_num(t) for t in re.findall(NUM, m.group("rest"))]
        nums = [n for n in nums if n is not None]
        if not nums:
            continue

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

    extracted = REPO / ticker / "Extracted"
    if not extracted.is_dir():
        print(f"no Extracted/ for {ticker} -- run pdftotext first", file=sys.stderr)
        return 1

    files = sorted(extracted.glob("*.txt"))
    facts = []
    for f in files:
        facts.extend(scan_file(f))

    import duckdb
    db = REPO / ticker / "Reports" / f"{ticker}.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    con.execute(schema.create_sql())
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
