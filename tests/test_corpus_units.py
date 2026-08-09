"""Every committed metrics CSV must say what scale and currency it is in.

This is the gate, not a unit test: it reads the live corpus, because the thing
being protected is the committed data rather than a function. Marked `slow`
for the same reason.

A number cannot reveal its own scale -- AAPL's revenue of 416,161 is millions
and 0285.HK's 179,477 is thousands -- so a CSV that does not declare its units
forces every consumer to guess. That guess is what read SEK.NZ as NZ$411bn of
revenue for a company making ~NZ$400m. `schema.metrics_normalized` resolves
unknown units to NULL rather than assuming, which makes the ticker vanish from
cross-ticker work instead of poisoning it, but the real fix is for the file to
say.

Run `make standardize-scale` to resolve and label; anything it cannot resolve
from a DCF declaration or agreeing anchors is listed here as a known
exception, not silently tolerated.
"""

import csv
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

# Tickers whose scale or currency could not be established from any
# independent source. They are excluded from the gate so it can protect
# everything else, and each one is a job to finish -- not a permanent waiver.
#
# Shrink this list; never grow it. A new ticker landing here means the
# research run did not record what it should have.
UNRESOLVED = {
    "1211.HK",   # no currency stated anywhere; filings vote RMB but the
                 # DCF is silent and MSN tags it BGN, so it is left blank
    "2CC.NZ",    # no currency in the DCF
    "AFI.NZ",    # DCF says "AUD (fundamentals) / NZD (reported valuation
                 # outputs)" -- not a code, and picking one is a guess
    "AGL.NZ",    # no units stated
    "DCBO",      # no units stated
    "PNG.V",     # no units stated
    "SDL.NZ",    # no units stated
    "SPK.NZ",    # DCF says "NZ$", a symbol rather than a code
}


def metrics_csvs() -> list[pathlib.Path]:
    return sorted(REPO.glob("research/*/Reports/*_Metrics.csv"))


def rows_with_data(path: pathlib.Path) -> list[dict[str, str]]:
    """Rows carrying at least one value, so a blank row is not a violation."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return [row for row in rows
            if any((v or "").strip() for k, v in row.items()
                   if k and k not in ("Period", "Units", "Currency"))]


@pytest.mark.slow
def test_every_populated_csv_declares_its_units_and_currency():
    offenders = []
    for path in metrics_csvs():
        ticker = path.parent.parent.name
        if ticker in UNRESOLVED:
            continue
        rows = rows_with_data(path)
        if not rows:
            continue
        if not any((r.get("Units") or "").strip() for r in rows):
            offenders.append(f"{ticker}: no Units")
        if not any((r.get("Currency") or "").strip() for r in rows):
            offenders.append(f"{ticker}: no Currency")

    assert not offenders, (
        "CSVs must declare their scale and currency -- run "
        "`make standardize-scale WRITE=1`:\n  " + "\n  ".join(offenders))


@pytest.mark.slow
def test_the_corpus_is_all_on_one_scale():
    # Cross-ticker maths is only safe if every file means the same thing by
    # a number. Anything not in millions has to be rescaled by every reader,
    # which is the bug surface this whole exercise removes.
    offenders = []
    for path in metrics_csvs():
        ticker = path.parent.parent.name
        if ticker in UNRESOLVED:
            continue
        for row in rows_with_data(path):
            units = (row.get("Units") or "").strip().lower()
            if units and units != "millions":
                offenders.append(f"{ticker}: {units}")
                break
    assert not offenders, (
        "every CSV should be in millions:\n  " + "\n  ".join(offenders))


# Companies that genuinely changed reporting currency mid-history. The break
# is real data, not a defect: Ebos Group moved from NZD to AUD at FY2019 and
# its revenue series is continuous across the change. Wise did the same
# GBP -> USD in FY2024.
CURRENCY_SWITCHERS = {"EBO.NZ", "WISE.L"}


@pytest.mark.slow
def test_units_never_change_within_a_file():
    # AIA.NZ's EPS column holds 0.37 (dollars) and 25.87 (cents) because
    # something changed convention mid-file. Unlike a currency change, a
    # scale change mid-file is never legitimate -- the file has one scale by
    # construction, so two values mean part of it is wrong.
    offenders = []
    for path in metrics_csvs():
        units = {(r.get("Units") or "").strip().lower()
                 for r in rows_with_data(path)}
        units.discard("")
        if len(units) > 1:
            offenders.append(f"{path.parent.parent.name}: {sorted(units)}")
    assert not offenders, "mixed units within a file:\n  " + "\n  ".join(offenders)


@pytest.mark.slow
def test_an_unexpected_currency_change_is_flagged():
    """A currency that changes mid-file is either a real switch or a bug.

    Ebos moved NZD -> AUD at FY2019 and Wise GBP -> USD at FY2024; both are
    genuine. Any *other* file that switches is reporting two currencies in
    one column, which makes every cross-year comparison in it wrong.
    """
    offenders = []
    for path in metrics_csvs():
        ticker = path.parent.parent.name
        if ticker in CURRENCY_SWITCHERS:
            continue
        currencies = {(r.get("Currency") or "").strip().upper()
                      for r in rows_with_data(path)}
        currencies.discard("")
        if len(currencies) > 1:
            offenders.append(f"{ticker}: {sorted(currencies)}")
    assert not offenders, (
        "currency changes mid-file -- verify it is a real reporting switch "
        "and add it to CURRENCY_SWITCHERS, or fix the data:\n  "
        + "\n  ".join(offenders))


@pytest.mark.slow
def test_the_unresolved_list_does_not_grow_silently():
    """Every waived ticker must still exist and still be unresolved.

    A stale entry means a ticker was fixed but the waiver stayed, which would
    hide the next regression on that file.
    """
    stale = []
    for ticker in sorted(UNRESOLVED):
        path = REPO / "research" / ticker / "Reports" / f"{ticker}_Metrics.csv"
        if not path.is_file():
            continue  # ticker removed from the corpus; harmless
        rows = rows_with_data(path)
        has_units = any((r.get("Units") or "").strip() for r in rows)
        has_currency = any((r.get("Currency") or "").strip() for r in rows)
        if has_units and has_currency:
            stale.append(ticker)
    assert not stale, (
        "these are now resolved -- remove them from UNRESOLVED:\n  "
        + "\n  ".join(stale))
