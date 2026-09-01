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
# Every name is anchored with \b at both ends. Without it the bare `yen`
# alternative matched the substring inside a company's own name
# ("BioSyent Inc.") and inside its CEO's ("Shantanu Narayen", 125 times in
# ADBE's filings), so both tickers were reported as reporting in JPY.
_NAMES = [
    (r"new zealand dollar", "NZD"),
    (r"australian dollar", "AUD"),
    (r"(?:u\.?s\.?|united states) dollar", "USD"),
    (r"(?:pound[s]? sterling|pound[s]?(?! sterling)|sterling)", "GBP"),
    (r"euro", "EUR"),
    (r"hong kong dollar", "HKD"),
    (r"singapore dollar", "SGD"),
    (r"canadian dollar", "CAD"),
    (r"(?:japanese )?yen", "JPY"),
    (r"swiss franc", "CHF"),
]
# The gap in the "currency ... is the" lead must not span a sentence OR a
# list of other currencies. ADBE's 10-Q says "exposed to foreign currencies,
# including the euro and the japanese yen" -- an FX-RISK disclosure. The old
# `[^.]{0,40}?` gap bridged "currencies, including the euro and the" and read
# it as a reporting-currency statement. Excluding commas and newlines from
# the gap keeps the lead to a single clause, which is the only place a filer
# actually names its reporting currency.
_LEADS = [
    r"presented in (?:thousands of |millions of )?",
    r"expressed in (?:thousands of |millions of )?",
    r"(?:reporting|presentation|functional) currency[^.,\n]{0,40}?is (?:the )?",
    r"amounts are in ",
]
# `\b` on both ends, with an optional plural `s` inside the trailing
# boundary -- filings say "New Zealand dollars", not "dollar".
STATED = [(lead + r"\b(?:" + name + r")s?\b", ccy)
          for lead in _LEADS for name, ccy in _NAMES]


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


def _dcf_currency(ticker: str) -> tuple[str | None, str | None, str | None]:
    """(currency, quote_currency, fx_note) from the ticker's DCF, if any.

    Absent fields read as None: 27 corpus DCFs predate the currency
    contract and record no currency at all, and a missing field is not
    evidence of a mismatch.
    """
    import json

    f = (REPO / "research" / ticker / "Reports" / f"{ticker}_DCF.json")
    if not f.is_file():
        return None, None, None
    try:
        inputs = (json.loads(f.read_text()) or {}).get("inputs") or {}
    except Exception:
        return None, None, None

    def code(value: object) -> str | None:
        # Bare ISO codes only. Prose ("AUD (fundamentals) / NZD (outputs)")
        # and symbols ("NZ$") are what quote_currency and fx_note replaced;
        # treating them as a code would invent a mismatch.
        text = value.strip() if isinstance(value, str) else ""
        return text if re.fullmatch(r"[A-Za-z]{3}|GBp", text) else None

    note = inputs.get("fx_note")
    return (code(inputs.get("currency")), code(inputs.get("quote_currency")),
            note if isinstance(note, str) and note.strip() else None)


def audit(only: list[str] | None = None) -> tuple[
        list[tuple[str, str, str]],
        list[tuple[str, str, str]],
        ]:
    """(problems, notes) for every ticker with a database.

    A *problem* is a recorded currency contradicted by what the filings
    STATE -- evidence, and worth the cost of a re-run.

    A *note* is a recorded currency that merely disagrees with the listing
    suffix while the filings state nothing. The suffix predicts the QUOTE
    currency; 16 of ~150 corpus tickers report in a currency they are not
    quoted in, so treating the suffix as evidence permanently demands
    re-runs of correct tickers (9999.HK, ARB.NZ and HFL.NZ were each
    flagged that way, and each is right).
    """
    import duckdb

    want = set(only or [])
    problems: list[tuple[str, str, str]] = []
    notes: list[tuple[str, str, str]] = []

    for db in sorted((REPO / "research").glob("*/Reports/*.duckdb")):
        ticker = db.parent.parent.name
        if want and ticker not in want:
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
        if len(currencies) > 1:
            # More than one distinct currency means adjudication conflated
            # periods reported in different currencies.
            problems.append((ticker, ", ".join(currencies), "one currency"))
            continue
        recorded = currencies[0]

        suffix = ticker.rsplit(".", 1)[1].upper() if "." in ticker else ""
        expect = BY_SUFFIX.get(suffix, "?")
        stated, _ = from_filings(ticker)

        if stated and recorded != stated:
            problems.append((ticker, recorded, stated))
            continue

        # The DB and the DCF each record a reporting currency, independently.
        # Nothing compared them until now, so BIT.NZ and FCT.NZ carried GBP
        # in core_metrics against NZD in the DCF -- a silent split inside the
        # very contract meant to prevent it, because the contract only ever
        # looked at one file.
        dcf_ccy, quote_ccy, fx_note = _dcf_currency(ticker)
        if dcf_ccy and recorded != dcf_ccy:
            problems.append((ticker, recorded, dcf_ccy))
            continue
        # Flows in one currency over a price in another, with no rate
        # recorded, is the quiet failure the contract names: the upside
        # looks plausible and is noise.
        if dcf_ccy and quote_ccy and dcf_ccy != quote_ccy and not fx_note:
            problems.append((ticker, f"{dcf_ccy} priced in {quote_ccy}",
                             "an fx_note"))
            continue

        if not stated and expect != "?" and recorded != expect:
            notes.append((ticker, recorded, expect))
    return problems, notes


def main() -> int:
    import duckdb
    only = set(sys.argv[1:])

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
        if len(currencies) > 1:
            print(f"  {ticker:10s} ambiguous: multiple currencies recorded "
                  f"({', '.join(currencies)})")
            continue
        recorded = currencies[0]

        suffix = ticker.rsplit(".", 1)[1].upper() if "." in ticker else ""
        expect = BY_SUFFIX.get(suffix, "?")
        stated, counts = from_filings(ticker)

        top = ", ".join(f"{c}x{n}" for c, n in
                        sorted(counts.items(), key=lambda kv: -kv[1])[:3])

        if stated and recorded != stated:
            flag = f"  <-- filings state {stated}"
        elif not stated and expect != "?" and recorded != expect:
            flag = f"  (note: {suffix} listings usually report {expect})"
        else:
            flag = ""

        print(f"  {ticker:10s} {recorded:>8s} {expect:>7s} "
              f"{(stated or '-'):>7s}  {top}{flag}")

    problems, notes = audit(sorted(only) or None)

    if problems:
        print(f"\n  {len(problems)} ticker(s) contradict their own filings "
              "-- re-run to correct:")
        for t, got, want_ccy in problems:
            print(f"    make run TICKER={t}      # recorded {got}, "
                  f"should be {want_ccy}")
    if notes:
        print(f"\n  {len(notes)} ticker(s) report in a currency they are not "
              "listed in.")
        print("  This is normal for a cross-listed filer -- confirm each is "
              "deliberate,")
        print("  and that its DCF records a quote_currency and an fx_note:")
        for t, got, expect_ccy in notes:
            print(f"    {t:12s} reports {got}, listed where {expect_ccy} "
                  "is usual")
    if not problems and not notes:
        print("\n  no currency mismatches")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
