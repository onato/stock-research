#!/usr/bin/env python3
"""Flag tickers whose recorded currency disagrees with their filings.

Currency is easy to get wrong and expensive when it is: WISE.L was
recorded as USD while its filings report in GBP, a ~1.27x error on every
figure, and cross-ticker comparison silently inherits it.

Two independent signals, neither trusted alone:

  * the listing suffix (.L -> GBP, .NZ -> NZD, bare -> USD) -- a good
    prior, but wrong for cross-listed filers
  * currency markers counted in the extracted filings, with an explicit
    "presented in X dollars" statement outranking symbol counts

Only the NEWEST filings are sampled, because reporting currency changes.
EBO.NZ says "presented in New Zealand dollars" in its FY2015 and FY2016
annual reports and reports in AUD from FY2025 (58 "AUD" mentions against
3 "NZD"). Grepping every year at once returns the old answer and looks
authoritative -- that mistake is why the sampling is deliberate.

Reports only -- fixing means re-running the ticker, since the CSV, DCF
and dashboard all carry the same currency.

Usage:
  check_currency.py            # every ticker with a database
  check_currency.py WISE.L     # one
"""

import collections
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

# Listing suffix -> the currency that listing normally reports in.
BY_SUFFIX = {
    "L": "GBP", "NZ": "NZD", "AX": "AUD", "HK": "HKD",
    "AS": "EUR", "PA": "EUR", "DE": "EUR", "BR": "EUR",
    "T": "JPY", "TO": "CAD", "V": "CAD", "SI": "SGD",
    "NS": "INR", "BO": "INR", "SW": "CHF", "ST": "SEK",
    "OL": "NOK", "CO": "DKK", "MC": "EUR", "MI": "EUR",
    "": "USD",
}

# Symbols as they appear in filing text.
SYMBOLS = [
    (r"NZ\$", "NZD"), (r"A\$", "AUD"), (r"US\$", "USD"),
    (r"HK\$", "HKD"), (r"S\$", "SGD"), (r"C\$", "CAD"),
    (r"£", "GBP"), (r"€", "EUR"), (r"¥", "JPY"), (r"₹", "INR"),
]

# An explicit statement of reporting currency outranks symbol counts.
#
# Both orderings occur and both must match:
#   "presented in New Zealand dollars"                     (currency last)
#   "The reporting currency of the Group is the U.S. dollar" (currency last,
#                                                             longer gap)
# WISE.L's FY2026 20-F uses the second form with "U.S." punctuated, which an
# earlier pattern set missed entirely -- so the checker fell back to counting
# stray GBP symbols and confidently reported the wrong answer.
_NAMES = [
    (r"new zealand dollar", "NZD"),
    (r"australian dollar", "AUD"),
    (r"(?:u\.?s\.?|united states) dollar", "USD"),
    (r"(?:pound[s]? sterling|pound[s]?(?! sterling)|sterling)", "GBP"),
    (r"euro", "EUR"),
    (r"hong kong dollar", "HKD"),
    (r"singapore dollar", "SGD"),
    (r"canadian dollar", "CAD"),
    (r"japanese yen|yen", "JPY"),
    (r"swiss franc", "CHF"),
]
_LEADS = [
    r"presented in (?:thousands of |millions of )?",
    r"expressed in (?:thousands of |millions of )?",
    r"(?:reporting|presentation|functional) currency[^.]{0,40}?is (?:the )?",
    r"amounts are in ",
]
STATED = [(lead + name, ccy) for lead in _LEADS for name, ccy in _NAMES]


def from_filings(ticker: str, sample: int = 5) -> tuple[str | None, dict[str, int]]:
    """(stated_currency, symbol_counts) from the extracted text."""
    d = REPO / "research" / ticker / "Extracted"
    files = sorted(d.glob("*.txt")) if d.is_dir() else []
    if not files:
        return None, {}
    # Statutory filings state the currency; presentations rarely do.
    statutory = [f for f in files if any(
        k in f.name.lower() for k in
        ("annual", "halfyear", "half-year", "interim", "results", "10k", "20f"))]

    # Sort by the PERIOD in the filename, not the filename itself. Sorting
    # alphabetically put WISE.L_Annual_FY2026 (the filing that announced the
    # move to USD reporting) before HalfYear and Results, so the newest and
    # most authoritative document fell outside the sample and the checker
    # reported GBP with high confidence. "Newest" has to mean newest period.
    def period_key(path: pathlib.Path) -> tuple[int, int, str]:
        m = re.search(r"(?:FY|H[12][-_]?|Q[1-4][-_]?)(\d{4})", path.name, re.IGNORECASE)
        year = int(m.group(1)) if m else 0
        # An annual report states the reporting currency; interims often
        # only reference it, so prefer annuals within the same year.
        rank = 2 if re.search(r"annual|10k|20f", path.name, re.IGNORECASE) else 1
        return (year, rank, path.name)

    picked = sorted(statutory or files, key=period_key)[-sample:]

    # Read newest-first and stop at the first filing that states a currency.
    # Pooling across years is wrong when a filer switches: WISE.L's older
    # reports carry 500+ GBP symbols while the FY2026 20-F is USD, so any
    # aggregate says GBP with false confidence. The most recent statutory
    # filing is the authority; older ones describe a currency that no
    # longer applies.
    counts: collections.Counter[str] = collections.Counter()
    stated: str | None = None
    for f in reversed(picked):
        text = f.read_text(errors="replace")
        low = text.lower()
        for pat, ccy in STATED:
            if re.search(pat, low):
                stated = ccy
                break
        for pat, ccy in SYMBOLS:
            n = len(re.findall(pat + r"\s?[\d,]", text))
            if n:
                counts[ccy] += n
        # One filing is enough once it names its currency, or once it has
        # given us symbol evidence to judge by.
        if stated or counts:
            break
    return stated, dict(counts)


def main() -> int:
    import duckdb
    only = set(sys.argv[1:])
    problems: list[tuple[str, str, str]] = []
    ambiguous: list[tuple[str, list[str]]] = []

    print(f"  {'ticker':10s} {'recorded':>8s} {'suffix':>7s} {'stated':>7s}  filing symbols")
    print("  " + "-" * 68)

    for db in sorted((REPO / "research").glob("*/Reports/*.duckdb")):
        ticker = db.parent.parent.name
        if only and ticker not in only:
            continue
        try:
            con = duckdb.connect(str(db), read_only=True)
            rows = con.execute(
                "SELECT DISTINCT currency FROM core_metrics "
                "WHERE currency IS NOT NULL").fetchall()
            con.close()
        except Exception:
            continue
        if not rows:
            continue
        currencies = sorted(r[0] for r in rows)
        # More than one distinct currency means adjudication conflated
        # periods reported in different currencies. Reporting whichever
        # row came first would let that pass as agreement, so flag it.
        if len(currencies) > 1:
            ambiguous.append((ticker, currencies))
            print(f"  {ticker:10s} ambiguous: multiple currencies recorded "
                  f"({', '.join(currencies)})")
            continue
        recorded = currencies[0]

        suffix = ticker.rsplit(".", 1)[1].upper() if "." in ticker else ""
        expect = BY_SUFFIX.get(suffix, "?")
        stated, counts = from_filings(ticker)

        top = ", ".join(f"{c}x{n}" for c, n in
                        sorted(counts.items(), key=lambda kv: -kv[1])[:3])

        # Trust an explicit statement over the suffix prior.
        truth = stated or expect
        bad = truth != "?" and recorded != truth
        flag = f"  <-- expected {truth}" if bad else ""
        if bad:
            problems.append((ticker, recorded, truth))

        print(f"  {ticker:10s} {recorded:>8s} {expect:>7s} "
              f"{(stated or '-'):>7s}  {top}{flag}")

    if problems:
        print(f"\n  {len(problems)} ticker(s) need a re-run to correct currency:")
        for t, got, want in problems:
            print(f"    make run TICKER={t}      # recorded {got}, should be {want}")
    if ambiguous:
        print(f"\n  {len(ambiguous)} ticker(s) record multiple currencies "
              "(re-run to re-adjudicate):")
        for t, ccys in ambiguous:
            print(f"    make run TICKER={t}      # recorded {', '.join(ccys)}")
    if not problems and not ambiguous:
        print("\n  no currency mismatches")
    return 1 if problems or ambiguous else 0


if __name__ == "__main__":
    sys.exit(main())
