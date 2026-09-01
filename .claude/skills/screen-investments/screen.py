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
import html
import json
import os
import pathlib
import re
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


def price_symbol(root, ticker):
    """The Yahoo symbol to quote for this ticker -- usually its own name.

    The folder name is not always the tradeable symbol. BGI.NZ was renamed
    to RTO.NZ on 1-May-2024, so Yahoo serves BGI.NZ as a dead symbol frozen
    at $0.004 while RTO.NZ trades at $0.119. DOW.NZ is Downer EDI's
    near-dormant NZX secondary line quoting $0.00063 NZD, while the DCF is
    built on the ASX primary at ~$7.80 AUD -- dividing an AUD intrinsic
    value by that NZD quote put DOW.NZ first on the leaderboard at +26,884%.

    `aliases` in info.json is deliberately NOT consulted: it is search
    metadata listing former names and cross-listings, and treating any of
    them as the quote source would silently reprice a ticker against a
    different listing. Only an explicit `price_symbol` redirects.
    """
    try:
        info = json.loads((pathlib.Path(root) / ticker
                           / "info.json").read_text())
        symbol = info.get("price_symbol")
    except (OSError, json.JSONDecodeError, AttributeError):
        return ticker
    return symbol.strip() if isinstance(symbol, str) and symbol.strip() \
        else ticker


CCY_RE = re.compile(r"^[A-Za-z]{3}$")


def currency_code(*candidates):
    """The first candidate that is an actual currency code.

    Two DCFs describe a mixed denomination in prose rather than a code:
    HFL.NZ carries "NZD (outputs) / GBP (fundamentals)" and AFI.NZ "AUD
    (fundamentals) / NZD (reported valuation outputs)". Printed verbatim,
    that is a 34-character cell in a column where every other row is three,
    and it stretches the whole leaderboard past the viewport -- and it makes
    the CCY flag unreadable too.

    Both carry a clean top-level `currency`, so the code is recoverable.
    Prose is never returned: an unlabelled price beats a wrong or unreadable
    label, and the mixed denomination is a property of the model that a
    three-letter cell cannot honestly convey anyway.
    """
    for value in candidates:
        if isinstance(value, str) and CCY_RE.match(value.strip()):
            return value.strip()
    return None


def load_dcf(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# Why a row is never in question, however large its ranked upside would be.
# "Terminal" valuation models are ones where the DCF itself says this is not
# a going business being valued but a shell, wind-down or liquidation being
# scenario-weighted (BGI.NZ ranked #1 at +980% on a frozen $0.004 print).
TERMINAL_MODEL_WORDS = ("shell", "liquidation", "waterfall",
                        "wind_down", "wind-down", "going_concern")


def load_never(root):
    """{ticker: reason} from state/never_interested.txt (hand-maintained).

    Lines are `TICKER  reason...`; blank lines and #-comments are skipped.
    Like state/companies.json, the file lives beside the root as well as
    inside it (--root defaults to `research`, state/ sits next to it).
    """
    candidates = (os.path.join(root, "state", "never_interested.txt"),
                  os.path.join(os.path.dirname(os.path.abspath(root)),
                               "state", "never_interested.txt"))
    for path in candidates:
        try:
            with open(path) as f:
                out = {}
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(None, 1)
                    out[parts[0]] = (parts[1] if len(parts) > 1
                                     else "listed in never_interested.txt")
                return out
        except OSError:
            continue
    return {}


def exclusion_reason(ticker, dcf, iv, never):
    """Why this ticker is not under consideration, or None if it is."""
    if ticker in never:
        return never[ticker]
    method = str(dcf.get("valuation_method")
                 or dcf.get("valuation_model") or "").lower()
    for word in TERMINAL_MODEL_WORDS:
        if word in method:
            return f"terminal valuation model: {method}"
    if iv is not None and iv <= 0:
        return "equity worthless (weighted IV \u2264 0)"
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
    only = {t.strip() for t in args.only.split(",")} if args.only else None
    never = load_never(args.root)

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
        ccy = currency_code((d.get("inputs") or {}).get("currency"),
                            d.get("currency"))
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
            live_price, live_ccy = fetch_live_price(
                price_symbol(args.root, ticker))

        price = live_price or stored_price
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
        if iv is not None and price:
            # `iv` of 0.0 is a real verdict (CBD.NZ: receivership waterfall
            # leaves nil to equity), so test against None -- a truthiness
            # check demoted worthless-equity names to "no data".
            upside = round((iv / price - 1) * 100, 1)

        rows.append({
            "ticker": ticker,
            "excluded_reason": exclusion_reason(ticker, d, iv, never),
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

    # Three buckets: excluded (never in question) is carved out first, so a
    # dead shell's arithmetic "upside" cannot top the leaderboard; then
    # ranked (has upside) by upside desc; unranked (no comparable number) last.
    excluded = [r for r in rows if r.get("excluded_reason")]
    live = [r for r in rows if not r.get("excluded_reason")]
    ranked = [r for r in live if r.get("upside_pct") is not None]
    unranked = [r for r in live if r.get("upside_pct") is None]
    ranked.sort(key=lambda r: r["upside_pct"], reverse=True)
    excluded.sort(key=lambda r: r["ticker"])
    return ranked, unranked, excluded


def fmt_price(r):
    if r.get("price") is None:
        return "—"
    return f"{r['price']:.2f}"


# ---------------------------------------------------------------------------
# Static leaderboard page
# ---------------------------------------------------------------------------

def load_scores(root):
    """{ticker: eval summary} from the latest state/scores/{T}_{date}.json each.

    Lexicographic order on the ISO-dated filenames means the last file per
    ticker is the newest (same pattern as scripts/after_run.py). Missing dir
    (fresh clone, or run from elsewhere) just means no quality column.
    """
    scores_dir = os.path.join(root, "state", "scores")
    if not os.path.isdir(scores_dir):
        return {}
    out = {}
    for name in sorted(os.listdir(scores_dir)):
        if not name.endswith(".json"):
            continue
        card = load_dcf(os.path.join(scores_dir, name))
        if card and isinstance(card.get("summary"), dict):
            out[name.rsplit("_", 1)[0]] = card["summary"]
    return out


def load_companies(root):
    """{ticker: {name, sector}} from state/companies.json (maintained by the
    research-stock skill). Missing file just means bare tickers on the page.

    `state/` sits BESIDE `research/`, but --root defaults to `research`, so
    the file is looked for next to the root as well as inside it. Joining
    only onto the root blanked the company column for every row.
    """
    candidates = (os.path.join(root, "state", "companies.json"),
                  os.path.join(os.path.dirname(os.path.abspath(root)),
                               "state", "companies.json"))
    for path in candidates:
        data = load_dcf(path)
        if isinstance(data, dict) and data:
            return data
    return {}


# Sentinel that sinks "—" cells to the bottom of any descending numeric sort.
SORT_MISSING = "-1e18"

FLAG_MEANINGS = (
    ("STALE", "DCF or Analysis older than the staleness threshold"),
    ("PRICE_DRIFT", "live price far from the price the DCF was built on — stored upside unreliable"),
    ("NO_PRICE", "no usable price, live or stored"),
    ("NO_IV", "DCF has no probability-weighted intrinsic value"),
    ("NO_DCF", "tracked company without a DCF model yet"),
    ("CCY", "DCF currency differs from the quote currency — upside mixes currencies"),
)


def esc(x):
    return html.escape(str(x))


def badge_html(flag):
    kind = flag.split("(")[0]
    cls = "bad" if kind in ("NO_PRICE", "NO_IV") else "warn"
    return f'<span class="badge {cls}">{esc(flag)}</span>'


def num_td(value, text, cls=""):
    sort = SORT_MISSING if value is None else f"{value}"
    cls_attr = f' class="{cls}"' if cls else ""
    return f'<td data-sort="{esc(sort)}"{cls_attr}>{text}</td>'


# Where ticker directories live, relative to index.html. Set from --root so
# regenerating the index cannot silently revert the links: a run that emitted
# bare "{TICKER}/Reports/..." after the move to research/ broke all 136 of
# them, and index.html is generated output, so the damage returns on every run
# until the generator itself knows the layout.
HREF_PREFIX = "research/"   # overridden from --root in main()


def dashboard_href(t):
    return f"{HREF_PREFIX}{esc(t)}/Reports/{esc(t)}_Dashboard.html"


def tr_open(t, co):
    """Row opener carrying the click-through target and the search haystack."""
    hay = " ".join(x for x in (t, co.get("name"), co.get("sector")) if x).lower()
    return f'<tr data-href="{dashboard_href(t)}" data-search="{esc(hay)}">'


def company_td(co):
    name = co.get("name") or ""
    sector = co.get("sector") or ""
    inner = esc(name) or "—"
    if sector:
        inner += f'<br><span class="cur">{esc(sector)}</span>'
    return f'<td class="co">{inner}</td>'


def row_html(i, r, summary, co):
    t = r.get("ticker", "?")
    cur = r.get("currency")
    live_cur = r.get("live_currency")
    shown_cur = live_cur or cur or ""

    flags = list(r.get("flags") or [])
    if cur and live_cur and cur != live_cur:
        flags.append(f"CCY({cur}→{live_cur})")

    price_txt = esc(fmt_price(r))
    if shown_cur:
        price_txt += f' <span class="cur">{esc(shown_cur)}</span>'
    price_cell = (f'<td data-sort="{esc(r["price"] if r.get("price") is not None else SORT_MISSING)}"'
                  f' title="{esc(r.get("price_src", ""))} price">{price_txt}</td>')

    iv = r.get("weighted_iv")
    iv_txt = f"{iv:.2f}" if iv is not None else "—"
    if iv is not None and cur:
        iv_txt = f'{iv_txt} <span class="cur">{esc(cur)}</span>'

    up = r.get("upside_pct")
    up_txt = f"{up:+.1f}%" if up is not None else "—"

    age = r.get("days_old")

    if summary:
        s_txt = f"{summary.get('score')}" if summary.get("score") is not None else "—"
        nf, nw = summary.get("fail", 0), summary.get("warn", 0)
        if nf or nw:
            s_txt += f' <span class="cur">({nf}F/{nw}W)</span>'
        s_cls = "neg" if nf else ("warn-text" if nw else "pos")
        score_cell = num_td(summary.get("score"), s_txt, s_cls)
    else:
        score_cell = num_td(None, "—")

    return tr_open(t, co) + "".join((
        num_td(i, str(i)),
        f'<td><a href="{dashboard_href(t)}">{esc(t)}</a></td>',
        company_td(co),
        price_cell,
        num_td(iv, iv_txt),
        num_td(up, esc(up_txt), "pos" if (up or 0) >= 0 else "neg"),
        num_td(age, f"{age}d" if age is not None else "—"),
        "<td>" + ("".join(badge_html(f) for f in flags) or '<span class="ok">ok</span>') + "</td>",
        score_cell,
    )) + "</tr>"


def write_html(ranked, unranked, excluded, meta, scores, companies, path):
    head = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Stock Research</title>
<meta name="description" content="DCF leaderboard and per-ticker research dashboards.">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#16213e">
<meta name="color-scheme" content="dark">
<link rel="icon" href="icons/icon.svg" type="image/svg+xml">
<link rel="icon" href="icons/favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Stocks">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html {{
    /* Paints the overscroll/safe-area gutters too; body alone leaves them
       white when the standalone PWA rubber-bands past the content. */
    background: #16213e;
    -webkit-text-size-adjust: 100%;
}}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    background-attachment: fixed;
    color: #e0e0e0; min-height: 100vh; max-width: 1100px;
    margin: 0 auto;
    /* viewport-fit=cover + black-translucent status bar: keep content clear
       of the notch and the home indicator. */
    padding: max(20px, env(safe-area-inset-top)) max(20px, env(safe-area-inset-right))
             max(20px, env(safe-area-inset-bottom)) max(20px, env(safe-area-inset-left));
}}
.header {{
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px; padding: 20px 25px; margin-bottom: 20px;
}}
h1 {{
    font-size: 1.6em;
    background: linear-gradient(90deg, #00d4aa, #00b894);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
}}
.meta {{ color: #8b8ba0; margin-top: 6px; font-size: 0.9em; }}
a {{ color: #00d4aa; text-decoration: none; }}
a:hover {{ color: #00b894; }}
.card {{
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px; padding: 10px; margin-bottom: 20px; overflow-x: auto;
    -webkit-overflow-scrolling: touch; overscroll-behavior-x: contain;
}}
table {{ border-collapse: collapse; width: 100%; font-size: 0.92em; }}
th {{
    color: #00d4aa; text-align: left; cursor: pointer; user-select: none;
    padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.15);
    white-space: nowrap;
}}
th.asc::after {{ content: " \\25B2"; font-size: 0.8em; }}
th.desc::after {{ content: " \\25BC"; font-size: 0.8em; }}
td {{ padding: 7px 10px; border-bottom: 1px solid rgba(255,255,255,0.06); white-space: nowrap; }}
tbody tr {{ cursor: pointer; }}
tr:hover td {{ background: rgba(255,255,255,0.04); }}
tr.hl td {{ background: rgba(0,212,170,0.10); }}
td.co {{ white-space: normal; max-width: 280px; line-height: 1.3; }}
.controls {{ display: flex; align-items: center; gap: 15px; margin-bottom: 20px; }}
.search-box {{
    flex: 1; padding: 12px 16px; font-size: 1em; color: #e0e0e0;
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px; outline: none;
}}
.search-box:focus {{ border-color: #00d4aa; }}
/* iOS zooms the page in when a focused input is under 16px. */
.search-box {{ font-size: 16px; }}

@media (max-width: 700px) {{
    body {{ padding-left: max(12px, env(safe-area-inset-left));
            padding-right: max(12px, env(safe-area-inset-right)); }}
    .header {{ padding: 16px; }}
    h1 {{ font-size: 1.3em; }}
    .card {{ padding: 6px; border-radius: 10px; }}
    table {{ font-size: 0.86em; }}
    th, td {{ padding: 10px 8px; }}
    /* Touch targets: a 7px-padded row is under the 44px minimum. */
    tbody tr td {{ padding-top: 12px; padding-bottom: 12px; }}
    td.co {{ max-width: 150px; }}
    .controls {{ flex-wrap: wrap; gap: 10px; }}
    /* The leaderboard keeps Ticker/Company/Price/IV/Upside; the rest is
       reachable by scrolling the card sideways, not lost. */
    .header .meta {{ font-size: 0.82em; }}
}}
.count {{ color: #8b8ba0; white-space: nowrap; }}
.pos {{ color: #00d4aa; }}
.neg {{ color: #ff6b6b; }}
.warn-text {{ color: #ffb400; }}
.ok {{ color: #8b8ba0; font-size: 0.85em; }}
.cur {{ color: #8b8ba0; font-size: 0.82em; }}
.badge {{
    display: inline-block; font-size: 0.72em; padding: 2px 6px;
    border-radius: 8px; margin-right: 4px;
}}
.badge.warn {{ background: rgba(255,180,0,0.15); color: #ffb400; }}
.badge.bad  {{ background: rgba(255,107,107,0.15); color: #ff6b6b; }}
h2 {{ font-size: 1.1em; color: #00d4aa; margin: 5px 0 10px 5px; }}
.footnote {{ color: #8b8ba0; font-size: 0.82em; line-height: 1.6; padding: 0 5px; }}
</style>
</head>
<body>
<div class="header">
<h1>Stock Research</h1>
<p class="meta">{len(companies) or len(ranked) + len(unranked)} companies tracked &nbsp;&middot;&nbsp;
as of {esc(meta["generated_at"])} &nbsp;&middot;&nbsp;
{"LIVE" if meta["live"] else "STORED"} prices &nbsp;&middot;&nbsp;
{len(ranked)} ranked / {len(unranked)} unranked / {len(excluded)} not under consideration &nbsp;&middot;&nbsp;
upside = weighted IV / price &minus; 1</p>
</div>
<div class="controls">
<input type="text" class="search-box" id="search" autofocus
 placeholder="Search by ticker, company, or sector&hellip; (Enter opens a single match)">
<span class="count" id="count"></span>
</div>
"""
    if ranked:
        body_rows = "\n".join(
            row_html(i, r, scores.get(r.get("ticker")),
                     companies.get(r.get("ticker"), {}))
            for i, r in enumerate(ranked, 1))
    else:
        body_rows = '<tr><td colspan="9">No ranked tickers — see unranked below.</td></tr>'

    ranked_table = f"""<div class="card">
<table id="lb">
<thead><tr>
<th data-type="num">#</th><th data-type="str">Ticker</th>
<th data-type="str">Company</th>
<th data-type="num">Price</th><th data-type="num">Weighted IV</th>
<th data-type="num">Upside</th><th data-type="num">Age</th>
<th data-type="str">Flags</th><th data-type="num">Eval</th>
</tr></thead>
<tbody>
{body_rows}
</tbody>
</table>
</div>
"""
    unranked_html = ""
    if unranked:
        u_rows = "\n".join(
            tr_open(r.get("ticker", "?"), companies.get(r.get("ticker"), {}))
            + f'<td><a href="{dashboard_href(r.get("ticker", "?"))}">{esc(r.get("ticker", "?"))}</a></td>'
            + company_td(companies.get(r.get("ticker"), {}))
            + f"<td>{''.join(badge_html(f) for f in (r.get('flags') or [])) or '—'}</td>"
            + f"<td>{esc(fmt_price(r))}</td>"
            + f"<td>{esc(r.get('note', ''))}</td>"
            + "</tr>"
            for r in unranked)
        unranked_html = f"""<h2>Not ranked</h2>
<div class="card">
<table>
<thead><tr><th>Ticker</th><th>Company</th><th>Flags</th><th>Price</th><th>Note</th></tr></thead>
<tbody>
{u_rows}
</tbody>
</table>
</div>
"""
    excluded_html = ""
    if excluded:
        x_rows = "\n".join(
            tr_open(r.get("ticker", "?"), companies.get(r.get("ticker"), {}))
            + f'<td><a href="{dashboard_href(r.get("ticker", "?"))}">{esc(r.get("ticker", "?"))}</a></td>'
            + company_td(companies.get(r.get("ticker"), {}))
            + f"<td>{esc(fmt_price(r))}</td>"
            + num_td(r.get("weighted_iv"),
                     f"{r['weighted_iv']:.2f}" if r.get("weighted_iv") is not None else "&mdash;")
            + f'<td class="co">{esc(r.get("excluded_reason", ""))}</td>'
            + "</tr>"
            for r in excluded)
        excluded_html = f"""<h2>Not under consideration</h2>
<div class="card">
<table>
<thead><tr><th>Ticker</th><th>Company</th><th>Price</th><th>Weighted IV</th><th>Why never</th></tr></thead>
<tbody>
{x_rows}
</tbody>
</table>
<p class="footnote">Dead shells, liquidations and worthless equity are excluded
automatically from the DCF's own verdict; state/never_interested.txt adds
names by hand (one <code>TICKER&nbsp;&nbsp;reason</code> per line). These rows
never enter the ranking.</p>
</div>
"""

    footnote = "<p class=\"footnote\">" + " &middot; ".join(
        f"<b>{esc(k)}</b>: {esc(v)}" for k, v in FLAG_MEANINGS
    ) + (" &middot; <b>Eval</b>: tier-1 scorecard pass rate (F fails / W warns), "
         "from state/scores/.</p>")

    sort_js = """<script>
// Row click-through (anchors inside still work for open-in-new-tab).
document.querySelectorAll('tbody tr[data-href]').forEach(tr => {
    tr.addEventListener('click', (e) => {
        if (e.target.closest('a')) return;
        window.location = tr.dataset.href;
    });
});

// Live search across both tables; Enter opens a lone match.
const rows = Array.from(document.querySelectorAll('tbody tr[data-search]'));
const count = document.getElementById('count');
const search = document.getElementById('search');
const applyFilter = () => {
    const q = search.value.trim().toLowerCase();
    const visible = [];
    rows.forEach(tr => {
        const show = !q || tr.dataset.search.includes(q);
        tr.style.display = show ? '' : 'none';
        tr.classList.remove('hl');
        if (show) visible.push(tr);
    });
    count.textContent = q ? `${visible.length} / ${rows.length}` : `${rows.length} tracked`;
    if (q && visible.length === 1) visible[0].classList.add('hl');
    return visible;
};
search.addEventListener('input', applyFilter);
search.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const visible = applyFilter();
    if (visible.length === 1) window.location = visible[0].dataset.href;
});
document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.activeElement === search) return;
    e.preventDefault();
    search.focus();
    search.select();
});
applyFilter();

document.querySelector('#lb thead').addEventListener('click', (e) => {
    const th = e.target.closest('th');
    if (!th) return;
    const idx = th.cellIndex, numeric = th.dataset.type === 'num';
    const dir = th.classList.contains(numeric ? 'desc' : 'asc')
        ? (numeric ? 'asc' : 'desc') : (numeric ? 'desc' : 'asc');
    th.parentNode.querySelectorAll('th').forEach(h => h.classList.remove('asc', 'desc'));
    th.classList.add(dir);
    const tbody = document.querySelector('#lb tbody');
    const key = row => {
        const td = row.cells[idx];
        return numeric ? parseFloat(td.dataset.sort ?? td.textContent) : td.textContent.trim();
    };
    Array.from(tbody.rows)
        .sort((a, b) => {
            const x = key(a), y = key(b);
            const c = numeric ? x - y : String(x).localeCompare(String(y));
            return dir === 'asc' ? c : -c;
        })
        .forEach(r => tbody.appendChild(r));
});

// Offline shell. Registration is best-effort: the page is fully functional
// without it, and file:// has no service-worker support at all.
if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('sw.js').catch(() => {});
    });
}
</script>
"""
    doc = (head + ranked_table + unranked_html + excluded_html + footnote
           + "\n" + sort_js + "</body>\n</html>\n")
    with open(path, "w") as f:
        f.write(doc)
    print(f"Index/leaderboard written to {path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="research")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--live", action="store_true", help="fetch live prices from Yahoo Finance")
    p.add_argument("--stale-days", type=int, default=45)
    p.add_argument("--drift-pct", type=float, default=15.0)
    p.add_argument("--only", default=None, help="comma-separated tickers to restrict to")
    p.add_argument("--json", default=None, help="write full results JSON to this path")
    p.add_argument("--html", default=None, help="write static leaderboard HTML to this path")
    args = p.parse_args()
    global HREF_PREFIX  # noqa: PLW0603 -- module-level link prefix, set once from --root
    _r = (args.root or ".").strip("/")
    HREF_PREFIX = "" if _r in ("", ".") else _r + "/"

    ranked, unranked, excluded = screen(args)
    meta = {"generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "live": args.live}

    print(f"\nInvestment screen — {today()}  ({'LIVE prices' if args.live else 'STORED prices'})")
    print("Upside = probability-weighted intrinsic value / price - 1\n")
    hdr = f"{'#':>2} {'TICKER':<9}{'PRICE':>9} {'src':<7}{'IV':>9}{'UPSIDE%':>9}  {'AGE':>5}  FLAGS"
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(ranked[: args.top], 1):
        age = f"{r['days_old']}d" if r.get("days_old") is not None else "?"
        iv = (f"{r['weighted_iv']:.2f}"
              if r.get("weighted_iv") is not None else "—")
        print(f"{i:>2} {r['ticker']:<9}{fmt_price(r):>9} {r['price_src']:<7}{iv:>9}"
              f"{r['upside_pct']:>8.1f}%  {age:>5}  {','.join(r['flags']) or 'ok'}")

    if unranked:
        print("\nNot ranked (need attention before they can be compared):")
        for r in unranked:
            print(f"   {r['ticker']:<9} {','.join(r['flags']) or 'no data'}"
                  + (f"  (price={fmt_price(r)})"
                     if r.get('price') is not None else ""))

    if excluded:
        print("\nNot under consideration (never in question):")
        for r in excluded:
            iv = (f"iv={r['weighted_iv']:.2f}"
                  if r.get("weighted_iv") is not None else "iv=\u2014")
            print(f"   {r['ticker']:<9} {iv:<10} {r.get('excluded_reason', '')}")

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
                       "excluded": excluded,
                       "generated": str(today()),
                       "generated_at": meta["generated_at"],
                       "live": args.live}, f, indent=2)
        print(f"\nFull results written to {args.json}")

    if args.html:
        companies = load_companies(args.root)
        # Tracked companies with no DCF yet still belong on the index page.
        covered = {r.get("ticker") for r in ranked + unranked + excluded}
        extras = [{"ticker": t, "flags": ["NO_DCF"], "note": "no DCF model yet"}
                  for t in sorted(companies)
                  if t not in covered
                  and os.path.isdir(os.path.join(args.root, t, "Reports"))]
        # A tracked-but-never ticker with no DCF still belongs at the bottom,
        # not in "Not ranked" where it reads as pending work.
        never = load_never(args.root)
        extra_excluded = [dict(e, excluded_reason=never[e["ticker"]])
                          for e in extras if e["ticker"] in never]
        extras = [e for e in extras if e["ticker"] not in never]
        write_html(ranked, unranked + extras,
                   sorted(excluded + extra_excluded, key=lambda r: r["ticker"]),
                   meta, load_scores(args.root), companies, args.html)


if __name__ == "__main__":
    main()
