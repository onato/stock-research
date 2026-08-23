"""Exchange-INDEPENDENT parsing vocabulary, shared by every parser.

Nothing in this module may encode one exchange's quirk: that belongs in a
BaseParser subclass (see base.py). What lives here is the metric vocabulary
(what a Revenue line is called), the number grammar, and the filename-period
convention — the parts that are the same in Auckland, Hong Kong and London.
"""

import re
from collections import Counter

# Metric -> regexes matched against the line label. Sourced from the search
# strings already documented in .claude/agents/financial-parser.md.
PATTERNS: dict[str, list[str]] = {
    # "(?!\s*tax)" because HLG.NZ's "Total income tax expense" line was
    # tagged as Revenue, poisoning the whole candidate set for the agent.
    "Revenue": [r"^(total |operating |net )?revenue", r"^net sales",
                r"^total income(?!\s*tax)"],
    "CostOfRevenue": [r"^cost of (sales|revenue|goods sold)"],
    "GrossProfit": [r"^gross (profit|margin|loss)"],
    "OperatingIncome": [r"^operating (profit|loss|income)(?! before)", r"^ebit\b",
                        r"^(profit|loss) from operations"],
    "EBITDA": [r"\bebitda\b",
               # "Operating profit before depreciation, amortisation, interest and tax"
               r"^operating (profit|loss) before (depreciation|interest|amortisation)"],   # adjusted/underlying/normalised all qualify
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
                          r"^cash flows? from operating"],
    "CapEx": [r"^(purchase|acquisition|additions?) of (property|plant|equipment|fixed)",
              r"^capital expenditure"],
    "Depreciation": [r"^depreciation( and amortisation| charge)?",
                     r"^amortisation( and impairment)?"],
    "ShareholdersEquity": [r"^total (shareholders.? )?equity", r"^net assets",
                           r"^equity attributable to"],
    "TotalAssets": [r"^total assets(?! less)"],
    "TotalLiabilities": [r"^total liabilities"],
    # Opening balances ("at 1 January", "brought forward") are last year's close.
    "CashAndEquivalents": [(r"^cash(,| and)( other)? (cash )?equivalents"
                            r"(?!.*(at 1 |at the beginning|at beginning|brought forward|at start))"),
                           r"^cash at bank", r"^bank (deposits|balances) and cash",
                           r"^cash and bank (balances|deposits)"],
    # Pre-tax, pre-interest subtotal -- a different quantity from OCF (0006.HK
    # FY2025: 547 vs 884). Its own metric so it can never outrank OCF.
    "CashGeneratedFromOperations": [r"^cash generated (from|by) operation"],
    "TotalDebt": [r"^(total )?borrowings", r"^(total )?(interest.bearing )?debt",
                  r"^loans and borrowings"],
    "SharesOutstanding": [# A count, not a transaction: "Shares purchased for Share Award Scheme" (0027.HK)
                          # and "Shares held for Share Award Scheme" (0388.HK) are cash flows.
                          r"^(weighted average )?number of (ordinary |issued )?shares",
                          r"^weighted average (number of )?(ordinary )?shares",
                          r"^(ordinary )?shares (in issue|outstanding)",
                          r"^issued (and fully paid )?(ordinary )?shares",
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
COMPILED: dict[str, list[re.Pattern[str]]] = {
    m: [re.compile(p, re.IGNORECASE) for p in pats] for m, pats in PATTERNS.items()}

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
LABEL_RE = re.compile(r"^[A-Za-z(][A-Za-z0-9 ,.&/()'’\-]{2,80}$")
# "(Loss)/profit for the year", "Earnings/(loss) per share": the sign-flip
# alternative is noise for vocabulary matching.
SIGN_FLIP_RE = re.compile(r"^\((?:loss|profit|deficit|surplus)\)/|/\((?:loss|profit|deficit|surplus)\)",
                          re.IGNORECASE)


BASIC_DILUTED_RE = re.compile(r"^(basic|diluted)( and diluted)?\b", re.IGNORECASE)


def normalize_label(label: str) -> str:
    """Drop "(Loss)/" style sign-flip alternatives so the vocabulary matches."""
    return SIGN_FLIP_RE.sub("", label).strip()


def parse_num(tok: str) -> float | None:
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


def split_lines(text: str) -> list[str]:
    """Split on newlines only. pdftotext puts a form feed at each page break
    and str.splitlines() would count it as a line, putting every line_no
    after the first page ahead of what `sed -n` / an editor shows."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
SPAN = {"year": 12, "twelve months": 12, "six months": 6, "half year": 6, "half-year": 6,
        "three months": 3, "quarter": 3, "nine months": 9}
_SPANS = "|".join(sorted(SPAN, key=len, reverse=True))
# "for the six months ended 31 December 2024" / "year ended December 31, 2024"
PERIOD_PHRASE_RE = re.compile(
    r"\b(" + _SPANS + r")\s+(?:period\s+)?ended\s+(?:on\s+)?(?:"
    r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9}),?\s+(\d{4})"
    r"|([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4}))"
    r"((?:\s+(?:19|20)\d{2}\b)*)", re.IGNORECASE)   # "..., 2013  2014  2015" column runs
MIN_PHRASE_VOTES = 3
# A period cited less than half as often as the most-cited one is a bond
# maturity, an outlook or a stray comparative, not the filing's own
# (ARB.NZ FY2016: 12 vs 2; 0363.HK: 166 vs 3). Among the rest the latest
# wins, which is how a mislabelled download (SPOT's FY2020 file is the 2018
# 20-F: 38 x 2018, 22 x 2017) gets its real year.
PHRASE_FLOOR_DIV = 2


def period_phrases(lines: list[str]) -> Counter[tuple[int, int, int]]:
    """(months, end_year, end_month) for every 'N months ended <date>' phrase."""
    votes: Counter[tuple[int, int, int]] = Counter()
    for line in lines:
        for m in PERIOD_PHRASE_RE.finditer(line):
            span = SPAN[m.group(1).lower()]
            month_name = (m.group(3) or m.group(5)).lower()
            year = int(m.group(4) or m.group(7))
            # US tables head several years under one date: the last is current.
            run = [int(y) for y in re.findall(r"(?:19|20)\d{2}", m.group(8) or "")]
            if run:
                year = max(year, *run)
            month = MONTHS.get(month_name) or MONTHS.get(month_name[:3])
            if month:
                votes[(span, year, month)] += 1
    return votes


def fiscal_year_end(lines: list[str]) -> int | None:
    """Month the fiscal year ends, from the file's own 'year ended' phrases."""
    annual = Counter({k: v for k, v in period_phrases(lines).items() if k[0] == 12})
    if not annual:
        return None
    (_, _, month), n = annual.most_common(1)[0]
    return month if n >= MIN_PHRASE_VOTES else None


def expected_span(name: str) -> int | None:
    """Months the filename's report type says the filing covers, or None."""
    if re.search(r"_(Annual|10K|20F|40F)_|_FY\d{4}", name, re.IGNORECASE) \
            and not re.search(r"_(HalfYear|Interim|Quarterly|10Q|6K)_", name, re.IGNORECASE):
        return 12
    if re.search(r"_(HalfYear|Interim)_|_H[12][-_ ]?\d{4}", name, re.IGNORECASE):
        return 6
    if re.search(r"_(Quarterly|10Q)_|_Q[1-4][-_ ]?\d{4}", name, re.IGNORECASE):
        return 3
    return None


def period_from_text(lines: list[str], fy_end_month: int | None = None,
                     expected_span: int | None = None) -> str | None:
    """The period the filing says it covers, canonically spelled, or None.

    An annual is FY<end year>. An interim needs the fiscal-year end to be
    named: six months to December is H1 of a June year and H2 of a December
    one. Fewer than MIN_PHRASE_VOTES mentions is a comparative or a quote,
    not the filing's own period.
    """
    votes = period_phrases(lines)
    if expected_span:
        # An interim cites "year ended ..." in its comparatives more often
        # than its own half-year line; only phrases of the filing's own
        # length count.
        votes = Counter({k: v for k, v in votes.items() if k[0] == expected_span})
    # The filing's own period is the latest one it reports at least
    # MIN_PHRASE_VOTES times; comparatives are cited more often but earlier.
    if not votes:
        return None
    top = max(votes.values())
    floor = max(MIN_PHRASE_VOTES, -(-top // PHRASE_FLOOR_DIV))   # ceil(top / 2)
    sure = [k for k, v in votes.items() if v >= floor]
    if not sure:
        return None
    span, year, month = max(sure, key=lambda k: (k[1], k[2]))
    if span == 12:
        return f"FY{year}"
    fye = fy_end_month or None
    if fye is None:
        return None
    fy = year if month <= fye else year + 1
    into = (month - fye) % 12 or 12        # months from the fiscal year start
    if span == 6:
        return f"H1 FY{fy}" if into == 6 else f"H2 FY{fy}" if into == 12 else None
    if span == 3:
        return f"Q{into // 3} FY{fy}" if into % 3 == 0 else None
    if span == 9:
        return f"9M FY{fy}" if into == 9 else None
    return None


def period_from_filename(name: str) -> str | None:
    """Filenames follow {TICKER}_{type}_{period}.txt (see CLAUDE.md)."""
    m = re.search(r"_(FY\d{4})", name, re.IGNORECASE)
    if m:
        # {T}_HalfYear_FY2025 names the half by its fiscal year; the period
        # is still the half. A quarterly file named this way does not say
        # which quarter, so it stays undated rather than guessed.
        if re.search(r"_(HalfYear|Interim)_", name, re.IGNORECASE):
            return f"H1-{m.group(1)[2:]}"
        if re.search(r"_Quarterly_", name, re.IGNORECASE):
            return None
        return m.group(1).upper()
    m = re.search(r"_(H[12])[-_ ]?(\d{4})", name, re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    m = re.search(r"_(Q[1-4])[-_ ]?(\d{4})", name, re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"
    return None
