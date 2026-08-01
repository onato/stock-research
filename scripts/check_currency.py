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

import pathlib
import re
import sys
import collections

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
STATED = [
    (r"presented in (?:thousands of |millions of )?new zealand dollars", "NZD"),
    (r"presented in (?:thousands of |millions of )?australian dollars", "AUD"),
    (r"presented in (?:thousands of |millions of )?(?:us|u\.s\.) dollars", "USD"),
    (r"presented in (?:thousands of |millions of )?(?:pounds|sterling)", "GBP"),
    (r"presented in (?:thousands of |millions of )?euros?", "EUR"),
    (r"reporting currency[^.]{0,30}new zealand", "NZD"),
    (r"reporting currency[^.]{0,30}australian", "AUD"),
    (r"reporting currency[^.]{0,30}(?:pound|sterling)", "GBP"),
    (r"functional currency[^.]{0,30}new zealand", "NZD"),
    (r"functional currency[^.]{0,30}(?:pound|sterling)", "GBP"),
]


def from_filings(ticker, sample=5):
    """(stated_currency, symbol_counts) from the extracted text."""
    d = REPO / "research" / ticker / "Extracted"
    files = sorted(d.glob("*.txt")) if d.is_dir() else []
    if not files:
        return None, {}
    # Statutory filings state the currency; presentations rarely do.
    statutory = [f for f in files if any(
        k in f.name.lower() for k in
        ("annual", "halfyear", "half-year", "interim", "results", "10k", "20f"))]
    picked = (statutory or files)[-sample:]

    counts = collections.Counter()
    stated = None
    for f in picked:
        text = f.read_text(errors="replace")
        low = text.lower()
        if stated is None:
            for pat, ccy in STATED:
                if re.search(pat, low):
                    stated = ccy
                    break
        for pat, ccy in SYMBOLS:
            n = len(re.findall(pat + r"\s?[\d,]", text))
            if n:
                counts[ccy] += n
    return stated, dict(counts)


def main():
    import duckdb
    only = set(sys.argv[1:])
    problems = []

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
        recorded = rows[0][0]

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
    else:
        print("\n  no currency mismatches")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
