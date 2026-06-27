#!/usr/bin/env python3
"""
Portfolio investment screener.

Ranks every ticker in the research folder by upside = intrinsic value / price - 1,
using the probability-weighted intrinsic value from each {TICKER}_DCF.json.

Optionally refreshes prices live from Yahoo Finance (same endpoint research-stock
uses) so the ranking reflects today's price rather than the (possibly stale) price
stored in the DCF file.

It flags, but does not silently drop:
  * STALE  -- DCF or Analysis older than --stale-days (default 45)
  * NO_IV  -- DCF has no probability-weighted intrinsic value
  * NO_PRICE -- no usable price (neither live nor stored)
  * PRICE_DRIFT -- live price differs from stored DCF price by > --drift-pct (default 15%)
                   i.e. the stored model is built off a materially different price

Usage:
  python3 screen.py [--root DIR] [--top N] [--live] [--stale-days 45]
                    [--drift-pct 15] [--json OUT.json] [--only T1,T2,...]

Without --live it uses prices stored in the DCF files (fast, fully offline).
With --live it fetches current prices for each ticker (slower, needs network).
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=1d&interval=1d"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def today():
    return dt.date.today()


def parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def days_old(datestr):
    d = parse_date(datestr)
    if not d:
        return None
    return (today() - d).days


def fetch_live_price(ticker):
    """Yahoo Finance last price. Returns (price, currency) or (None, None)."""
    url = YF_URL.format(t=urllib.parse.quote(ticker))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        meta = data["chart"]["result"][0]["meta"]
        return meta.get("regularMarketPrice"), meta.get("currency")
    except Exception:
        return None, None


def load_dcf(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def discover(root):
    """Yield (ticker, dcf_path, analysis_path) for every {T}/Reports/{T}_DCF.json."""
    for name in sorted(os.listdir(root)):
        rep = os.path.join(root, name, "Reports")
        if not os.path.isdir(rep):
            continue
        dcf = os.path.join(rep, f"{name}_DCF.json")
        if os.path.isfile(dcf):
            ana = os.path.join(rep, f"{name}_Analysis.json")
            yield name, dcf, (ana if os.path.isfile(ana) else None)


def screen(args):
    rows = []
    only = set(t.strip() for t in args.only.split(",")) if args.only else None

    for ticker, dcf_path, ana_path in discover(args.root):
        if only and ticker not in only:
            continue
        d = load_dcf(dcf_path)
        if d is None:
            rows.append({"ticker": ticker, "flags": ["NO_IV"], "note": "unreadable DCF"})
            continue

        pw = d.get("probability_weighted") or {}
        iv = pw.get("weighted_iv")
        stored_price = d.get("current_price")
        ccy = (d.get("inputs") or {}).get("currency")
        vdate = d.get("valuation_date")
        adate = None
        if ana_path:
            a = load_dcf(ana_path) or {}
            adate = a.get("analysis_date") or a.get("analysis_date")

        flags = []

        # staleness
        vold = days_old(vdate)
        aold = days_old(adate)
        oldest = max([x for x in (vold, aold) if x is not None], default=None)
        if oldest is not None and oldest > args.stale_days:
            flags.append(f"STALE({oldest}d)")

        # price selection
        live_price = live_ccy = None
        if args.live:
            live_price, live_ccy = fetch_live_price(ticker)

        price = live_price if live_price else stored_price
        price_src = "live" if live_price else "stored"
        if price is None:
            flags.append("NO_PRICE")

        # drift between live and stored
        if live_price and stored_price:
            drift = abs(live_price / stored_price - 1) * 100
            if drift > args.drift_pct:
                flags.append(f"PRICE_DRIFT({drift:.0f}%)")

        if iv is None:
            flags.append("NO_IV")

        upside = None
        if iv and price:
            upside = round((iv / price - 1) * 100, 1)

        rows.append({
            "ticker": ticker,
            "currency": ccy,
            "live_currency": live_ccy,
            "price": price,
            "price_src": price_src,
            "stored_price": stored_price,
            "live_price": live_price,
            "weighted_iv": iv,
            "upside_pct": upside,
            "valuation_date": vdate,
            "analysis_date": adate,
            "days_old": oldest,
            "flags": flags,
        })

    # rank: ranked (has upside) first by upside desc; unranked after
    ranked = [r for r in rows if r.get("upside_pct") is not None]
    unranked = [r for r in rows if r.get("upside_pct") is None]
    ranked.sort(key=lambda r: r["upside_pct"], reverse=True)
    return ranked, unranked


def fmt_price(r):
    if r.get("price") is None:
        return "—"
    return f"{r['price']:.2f}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=".")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--live", action="store_true", help="fetch live prices from Yahoo Finance")
    p.add_argument("--stale-days", type=int, default=45)
    p.add_argument("--drift-pct", type=float, default=15.0)
    p.add_argument("--only", default=None, help="comma-separated tickers to restrict to")
    p.add_argument("--json", default=None, help="write full results JSON to this path")
    args = p.parse_args()

    ranked, unranked = screen(args)

    print(f"\nInvestment screen — {today()}  ({'LIVE prices' if args.live else 'STORED prices'})")
    print("Upside = probability-weighted intrinsic value / price - 1\n")
    hdr = f"{'#':>2} {'TICKER':<9}{'PRICE':>9} {'src':<7}{'IV':>9}{'UPSIDE%':>9}  {'AGE':>5}  FLAGS"
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(ranked[: args.top], 1):
        age = f"{r['days_old']}d" if r.get("days_old") is not None else "?"
        iv = f"{r['weighted_iv']:.2f}" if r.get("weighted_iv") else "—"
        print(f"{i:>2} {r['ticker']:<9}{fmt_price(r):>9} {r['price_src']:<7}{iv:>9}"
              f"{r['upside_pct']:>8.1f}%  {age:>5}  {','.join(r['flags']) or 'ok'}")

    if unranked:
        print("\nNot ranked (need attention before they can be compared):")
        for r in unranked:
            print(f"   {r['ticker']:<9} {','.join(r['flags']) or 'no data'}"
                  + (f"  (price={fmt_price(r)})" if r.get('price') else ""))

    stale = [r for r in ranked if any(f.startswith('STALE') for f in r['flags'])]
    drift = [r for r in ranked if any(f.startswith('PRICE_DRIFT') for f in r['flags'])]
    if stale:
        print(f"\n⚠  {len(stale)} ranked names have STALE data (> {args.stale_days}d): "
              + ", ".join(r['ticker'] for r in stale))
    if drift:
        print(f"⚠  {len(drift)} names: live price drifted > {args.drift_pct:.0f}% from the price the DCF was built on "
              "— their stored upside is unreliable; re-run research-stock: "
              + ", ".join(r['ticker'] for r in drift))
    if not args.live:
        print("\nℹ  Ran on STORED prices. Pass --live to refresh prices from Yahoo Finance before ranking.")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"ranked": ranked, "unranked": unranked,
                       "generated": str(today()), "live": args.live}, f, indent=2)
        print(f"\nFull results written to {args.json}")


if __name__ == "__main__":
    main()
