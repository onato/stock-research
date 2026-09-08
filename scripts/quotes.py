#!/usr/bin/env python3
"""The one Yahoo Finance client for the repo.

Four call sites each carried their own urllib block -- screen.py,
prune_queue.py, dcf_context.py, and the orchestrator's inline `curl` in
SKILL.md Steps 8b/8c. They disagreed about the timeout (15s vs 20s), about
the User-Agent, and -- the difference that produced a wrong number -- about
whether `info.json:price_symbol` redirects the quote. refresh_price did not
apply it, so BGI.NZ (renamed RTO.NZ in May 2024, served by Yahoo as a dead
symbol frozen at $0.004) refreshed against the corpse.

Three calls, one contract:

    live(symbol)             -> Quote | None
    monthly(symbol, "10y")   -> [(date, close), ...]     (oldest first)
    fx("EURUSD=X")           -> float | None

**Nothing here raises.** Every failure -- DNS, a 429, a 500, malformed
JSON, a symbol Yahoo has never heard of -- returns None or an empty list.
That is not laziness about error handling: every caller's alternative to a
quote is to leave the stored number alone, and these run inside `--all`
sweeps over 163 tickers where one escaping exception loses the other 162.
Callers that need to distinguish "no such symbol" from "the network
blinked" -- prune_queue, which comments tickers OUT of the queue on that
evidence -- keep their own fetcher deliberately.

Usage:
  quotes.py SYMBOL            # one live quote
  quotes.py SYMBOL --monthly  # 10y of monthly closes, as CSV
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parents[1]

UA = "Mozilla/5.0 (stock-research)"
TIMEOUT = 20
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"


@dataclass(frozen=True)
class Quote:
    """A last-trade price with the currency it is denominated in.

    `currency` matters as much as `price`: WISE.L quotes GBp against USD
    filings, and dividing a USD intrinsic value by a pence quote yields a
    plausible-looking ratio that is pure coincidence.
    """

    price: float
    currency: str | None
    as_of: str | None
    market_state: str | None = None
    high_52w: float | None = None
    low_52w: float | None = None


def _get(url: str, timeout: int | None = None) -> str:
    """Fetch a URL as text. The single seam the tests replace."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body: bytes = r.read()
    return body.decode(errors="replace")


def _chart(symbol: str, **params: str) -> dict[str, object] | None:
    """The `chart.result[0]` object, or None on any failure whatsoever."""
    url = CHART_URL.format(t=urllib.parse.quote(symbol))
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        payload = _get(url, timeout=TIMEOUT)
        doc = json.loads(payload)
        chart = doc.get("chart") or {}
        if chart.get("error"):
            return None
        results = chart.get("result") or []
        if not results:
            return None
        first = results[0]
    except Exception:
        # Deliberately broad: see the module docstring. Anything urllib,
        # json, or a shape change on Yahoo's side can throw here, and no
        # caller has a use for the distinction.
        return None
    return first if isinstance(first, dict) else None


def _iso(epoch: object) -> str | None:
    if not isinstance(epoch, (int, float, str)):
        return None
    try:
        ts = dt.datetime.fromtimestamp(int(epoch), dt.UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return ts.isoformat().replace("+00:00", "Z")


def _float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def live(symbol: str) -> Quote | None:
    """The last trade for `symbol`, or None if Yahoo would not say."""
    result = _chart(symbol, range="1d", interval="1d")
    if result is None:
        return None
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return None
    price = _float(meta.get("regularMarketPrice"))
    if price is None:
        return None
    return Quote(price=price,
                 currency=_str(meta.get("currency")),
                 as_of=_iso(meta.get("regularMarketTime")),
                 market_state=_str(meta.get("marketState")),
                 high_52w=_float(meta.get("fiftyTwoWeekHigh")),
                 low_52w=_float(meta.get("fiftyTwoWeekLow")))


def monthly(symbol: str, range_: str = "10y") -> list[tuple[dt.date, float]]:
    """Monthly closes, oldest first. Empty on any failure.

    Months Yahoo has no close for (halted, pre-listing, a data gap) are
    dropped rather than carried forward: a fabricated price would silently
    become an FY-end anchor in the sanity check.
    """
    result = _chart(symbol, range=range_, interval="1mo")
    if result is None:
        return []
    stamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(stamps, list) or not isinstance(indicators, dict):
        return []
    series = indicators.get("quote") or []
    if not isinstance(series, list) or not series or not isinstance(series[0], dict):
        return []
    closes = series[0].get("close")
    if not isinstance(closes, list):
        return []
    rows: list[tuple[dt.date, float]] = []
    for stamp, close in zip(stamps, closes, strict=False):
        value = _float(close)
        if value is None:
            continue
        try:
            when = dt.datetime.fromtimestamp(int(stamp), dt.UTC).date()
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        rows.append((when, value))
    rows.sort(key=lambda r: r[0])
    return rows


def fx(pair: str) -> float | None:
    """A currency rate, e.g. fx("EURUSD=X") -> 1.0842. None on failure."""
    q = live(pair)
    return q.price if q else None


def price_symbol(root: pathlib.Path | str, ticker: str) -> str:
    """The Yahoo symbol to quote for `ticker` -- usually its own name.

    The folder name is not always the tradeable symbol. BGI.NZ was renamed
    RTO.NZ on 1-May-2024, so Yahoo serves BGI.NZ frozen at $0.004 while
    RTO.NZ trades at $0.119. DOW.NZ is Downer EDI's near-dormant NZX
    secondary line quoting $0.00063 NZD against an ASX-primary AUD DCF.

    `aliases` is deliberately NOT consulted: it is search metadata listing
    former names and cross-listings, and treating any of them as the quote
    source would silently reprice a ticker against a different listing.
    Only an explicit `price_symbol` redirects.
    """
    try:
        info = json.loads((pathlib.Path(root) / ticker / "info.json").read_text())
        symbol = info.get("price_symbol")
    except (OSError, json.JSONDecodeError, AttributeError):
        return ticker
    return symbol.strip() if isinstance(symbol, str) and symbol.strip() else ticker


def live_for_ticker(ticker: str, root: pathlib.Path | str | None = None) -> Quote | None:
    """`live()` through the ticker's `info.json:price_symbol` redirect."""
    base = pathlib.Path(root) if root is not None else REPO / "research"
    return live(price_symbol(base, ticker))


def monthly_for_ticker(ticker: str, root: pathlib.Path | str | None = None,
                       range_: str = "10y") -> list[tuple[dt.date, float]]:
    """`monthly()` through the ticker's `info.json:price_symbol` redirect."""
    base = pathlib.Path(root) if root is not None else REPO / "research"
    return monthly(price_symbol(base, ticker), range_=range_)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print("usage: quotes.py SYMBOL [--monthly]", file=sys.stderr)
        return 2
    want_monthly = "--monthly" in args
    symbol = next(a for a in args if not a.startswith("--"))
    if want_monthly:
        rows = monthly(symbol)
        if not rows:
            print("no data", file=sys.stderr)
            return 1
        print("Date,Close")
        for when, close in rows:
            print(f"{when.isoformat()},{close}")
        return 0
    q = live(symbol)
    if q is None:
        print("no data", file=sys.stderr)
        return 1
    print(f"{q.price} {q.currency or ''} as_of={q.as_of}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
