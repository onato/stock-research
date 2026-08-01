"""Exchange-INDEPENDENT parsing vocabulary, shared by every parser.

Nothing in this module may encode one exchange's quirk: that belongs in a
BaseParser subclass (see base.py). What lives here is the metric vocabulary
(what a Revenue line is called), the number grammar, and the filename-period
convention — the parts that are the same in Auckland, Hong Kong and London.
"""

import re

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
