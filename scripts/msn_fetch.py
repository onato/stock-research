#!/usr/bin/env python3
"""Fetch annual financials from MSN Money, cached on disk.

Why this source. The corpus's largest single gap is CapEx -- 271 of 896
missing core-8 cells -- and neither the SEC XBRL path nor the PDF parsers
reliably populate it. MSN publishes capitalExpenditures, operating cash flow
and the rest per fiscal year for US and non-US filers alike.

Why the *raw* API rather than the derived DuckDB in ~/Stocks/rule1: that
database drops the currency and disagrees with our filings on 17-22% of
comparable cells (EPS on 75%, from a cents/dollars mix-up). The API itself
states the currency of each statement, and spot-checking PINS and AIA.NZ
against the committed CSVs matched at exactly 1.0000 on every comparable
cell. The corruption is in the derivation, not the source.

Two rules this module enforces:

*The feed's currency is checked, never trusted.* BYD (1211.HK) comes back
tagged BGN -- Bulgarian Lev -- for a company reporting in CNY. Anything whose
currency contradicts what the CSV already records is refused rather than
converted, per the units convention in CLAUDE.md: a missing row is obvious, a
plausible wrong one is not.

*Everything is cached.* MSN rate limits, and a re-run that re-asks for data it
already has will get throttled. Payloads land in state/msn_cache/{TICKER}.json
with a fetch timestamp; misses are remembered too, so a ticker MSN does not
carry is not re-asked every run.

This module only *fetches*. It never writes a Metrics CSV -- reconciling a
third-party figure against filings-derived data is a judgment call, and it
belongs to the backfill-msn skill.

Usage:
  msn_fetch.py TICKER [TICKER ...]        # show what MSN has
  msn_fetch.py --json out.json PINS       # machine-readable
  msn_fetch.py --refresh PINS             # ignore the cache
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
CACHE = REPO / "state" / "msn_cache"

# Endpoints and key as used by ~/Stocks/rule1/src/msn_money_fetcher.py.
MSN_API_KEY = "0QfOX3Vn51YCzitbLaRkTTBadtWpgTN8NZLW0C1SEM"
AUTOSUGGEST = ("https://services.bingapis.com/"
               "contentservices-finance.csautosuggest/api/v1/Query")
FINANCIALS = "https://assets.msn.com/service/Finance/Equities/financialstatements"

MARKETS = {
    "NZ": "en-nz", "AX": "en-au", "TO": "en-ca", "L": "en-gb",
    "DE": "de-de", "PA": "fr-fr", "AS": "nl-nl", "T": "ja-jp",
    "HK": "zh-hk", "NS": "en-in", "SI": "en-sg", "OL": "no-no",
}

# Tickers the autosuggest search cannot resolve, carried over from rule1.
SEC_IDS = {"FLOW.AS": "alo7kr"}

# MSN's field paths -> the repo's canonical CSV headers. Only metrics the
# core schema knows: anything else belongs in a ticker's KPI columns and is
# not this module's business.
FIELDS: list[tuple[str, tuple[str, ...], bool]] = [
    # (csv header, path within a statement, scales with units)
    ("Revenue", ("incomeStatement", "revenue", "totalRevenue"), True),
    ("OperatingIncome", ("incomeStatement", "income", "operatingIncome"), True),
    ("NetIncome",
     ("incomeStatement", "income", "incomeAvailableToComExclExtraOrd"), True),
    ("TotalAssets", ("balanceSheets", "currentAssets", "totalAssets"), True),
    ("TotalLiabilities",
     ("balanceSheets", "currentLiabilities", "totalLiabilities"), True),
    ("TotalDebt", ("balanceSheets", "currentLiabilities", "totalDebt"), True),
    ("ShareholdersEquity", ("balanceSheets", "equity", "totalEquity"), True),
    ("OperatingCashFlow",
     ("cashFlow", "operating", "cashFromOperatingActivities"), True),
    ("CapEx", ("cashFlow", "investing", "capitalExpenditures"), True),
    # Per-share figures are scale-free.
    ("EPS", ("incomeStatement", "revenue", "dilutedNormalizedEPS"), False),
]

# The repo's canonical scale (see CLAUDE.md); MSN reports absolute units.
MILLIONS = 1e6

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class RateLimiter:
    """Spaces live calls so a bulk run does not get throttled."""

    def __init__(self, min_interval: float = 1.5) -> None:
        self.min_interval = min_interval
        self._last: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self._last is not None:
            gap = self.min_interval - (now - self._last)
            if gap > 0:
                time.sleep(gap)
        self._last = time.monotonic()


def market_for(ticker: str) -> str:
    """MSN market code for a ticker's listing suffix."""
    if "." in ticker:
        return MARKETS.get(ticker.rsplit(".", 1)[1].upper(), "en-us")
    return "en-us"


def _dig(obj: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def extract(payload: Any) -> dict[str, dict[str, Any]]:
    """Annual statements -> {"FY2024": {"Revenue": 3646.166, ...}}.

    Only annual rows: MSN's quarter labelling does not line up with the
    fiscal-quarter convention the rest of the repo uses, so claiming to know
    a quarter here would put values in the wrong period.
    """
    out: dict[str, dict[str, Any]] = {}
    if not payload:
        return out

    for statement in payload:
        if not isinstance(statement, dict):
            continue
        if statement.get("type") != "annual":
            continue
        year = statement.get("year")
        try:
            period = f"FY{int(str(year))}"
        except (TypeError, ValueError):
            continue

        row: dict[str, Any] = {}
        for header, path, scaled in FIELDS:
            value = _dig(statement, path)
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            row[header] = number / MILLIONS if scaled else number

        # FreeCashFlow is not published; derive it the way the CSVs do.
        # capitalExpenditures arrives as a negative investing outflow, so it
        # is added rather than subtracted.
        if "OperatingCashFlow" in row and "CapEx" in row:
            row["FreeCashFlow"] = row["OperatingCashFlow"] - abs(row["CapEx"])

        currency = (_dig(statement, ("incomeStatement", "currency"))
                    or _dig(statement, ("cashFlow", "currency"))
                    or _dig(statement, ("balanceSheets", "currency")))
        if currency:
            row["Currency"] = str(currency).strip().upper()
        if row:
            out[period] = row
    return out


def currency_conflict(rows: dict[str, dict[str, Any]],
                      recorded: str) -> tuple[str, str] | None:
    """(msn_currency, recorded) when the feed contradicts the CSV, else None.

    MSN tags BYD's statements BGN for a company reporting in CNY. Converting
    on a guess is what the units convention exists to prevent, so a caller is
    expected to refuse the ticker rather than reconcile it.
    """
    want = (recorded or "").strip().upper()
    if not want:
        return None
    for row in rows.values():
        got = (row.get("Currency") or "").strip().upper()
        if got and got != want:
            return (got, want)
    return None


def _safe_name(ticker: str) -> str:
    """Cache filename that cannot escape the cache directory."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker).lstrip(".") or "_"


def _get_json(url: str, params: dict[str, str], timeout: int = 20) -> Any:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def sec_id(ticker: str, limiter: RateLimiter | None = None) -> str | None:
    """Resolve a ticker to MSN's internal security id."""
    if ticker.upper() in SEC_IDS:
        return SEC_IDS[ticker.upper()]
    symbol = ticker.rsplit(".", 1)[0] if "." in ticker else ticker
    if limiter:
        limiter.wait()
    try:
        data = _get_json(AUTOSUGGEST, {"query": symbol,
                                       "market": market_for(ticker),
                                       "count": "1"})
        stocks = ((data or {}).get("data") or {}).get("stocks") or []
        if not stocks:
            return None
        first = stocks[0]
        parsed = json.loads(first) if isinstance(first, str) else first
        got = parsed.get("SecId")
        return str(got) if got else None
    except Exception:
        return None


def fetch(ticker: str, limiter: RateLimiter | None = None) -> Any:
    """Live MSN payload for a ticker, or None."""
    ident = sec_id(ticker, limiter)
    if not ident:
        return None
    if limiter:
        limiter.wait()
    try:
        return _get_json(FINANCIALS, {
            "apikey": MSN_API_KEY,
            "ocid": "finance-utils-peregrine",
            "cm": market_for(ticker),
            "it": "web",
            "scn": "ANON",
            "$filter": f"_p eq '{ident}'",
            "$top": "200",
            "wrapodata": "false",
        })
    except Exception:
        return None


def cached_payload(ticker: str, cache_dir: pathlib.Path,
                   fetch: Callable[[str], Any] = fetch,
                   max_age_days: int = 30,
                   remember_misses: bool = False) -> Any:
    """Payload for `ticker`, from disk when fresh enough.

    MSN rate limits, so re-asking for data already held is the thing to
    avoid. Annual financials change a few times a year at most, hence the
    generous default age.
    """
    cache_dir = pathlib.Path(cache_dir)
    path = cache_dir / f"{_safe_name(ticker)}.json"

    if path.is_file() and max_age_days > 0:
        try:
            blob = json.loads(path.read_text())
            stamp = dt.datetime.fromisoformat(blob["fetched_at"])
            if (dt.datetime.now() - stamp).days < max_age_days:
                return blob.get("payload")
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass  # unreadable or corrupt: treat as a miss

    payload = fetch(ticker)
    if payload is None and not remember_misses:
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "ticker": ticker,
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
    }))
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("tickers", nargs="+")
    p.add_argument("--cache", default=str(CACHE))
    p.add_argument("--max-age-days", type=int, default=30,
                   help="re-fetch a cache entry older than this")
    p.add_argument("--refresh", action="store_true",
                   help="ignore the cache entirely")
    p.add_argument("--min-interval", type=float, default=1.5,
                   help="seconds between live calls")
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args()

    limiter = RateLimiter(args.min_interval)
    cache_dir = pathlib.Path(args.cache)
    result: dict[str, Any] = {}

    for ticker in args.tickers:
        payload = cached_payload(
            ticker, cache_dir,
            fetch=lambda t: fetch(t, limiter),
            max_age_days=0 if args.refresh else args.max_age_days,
            remember_misses=True)
        rows = extract(payload)
        result[ticker] = rows
        if not rows:
            print(f"{ticker}: no annual data from MSN", file=sys.stderr)
            continue
        currencies = {r.get("Currency") for r in rows.values() if r.get("Currency")}
        print(f"{ticker}: {len(rows)} annual period(s), "
              f"currency {'/'.join(sorted(c for c in currencies if c)) or '?'}")
        for period in sorted(rows):
            row = rows[period]
            bits = ", ".join(
                f"{k}={v:,.2f}" for k, v in sorted(row.items())
                if k != "Currency" and isinstance(v, float))
            print(f"  {period}  {bits}")

    if args.json_out:
        out = pathlib.Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1))
        print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
