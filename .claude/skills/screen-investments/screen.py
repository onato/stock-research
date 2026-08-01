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
    only = {t.strip() for t in args.only.split(",")} if args.only else None

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
    research-stock skill). Missing file just means bare tickers on the page."""
    data = load_dcf(os.path.join(root, "state", "companies.json"))
    return data if isinstance(data, dict) else {}


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


def write_html(ranked, unranked, meta, scores, companies, path):
    head = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Research</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    color: #e0e0e0; min-height: 100vh; max-width: 1100px;
    margin: 0 auto; padding: 20px;
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
{len(ranked)} ranked / {len(unranked)} unranked &nbsp;&middot;&nbsp;
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
</script>
"""
    doc = head + ranked_table + unranked_html + footnote + "\n" + sort_js + "</body>\n</html>\n"
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

    ranked, unranked = screen(args)
    meta = {"generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "live": args.live}

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
                       "generated": str(today()),
                       "generated_at": meta["generated_at"],
                       "live": args.live}, f, indent=2)
        print(f"\nFull results written to {args.json}")

    if args.html:
        companies = load_companies(args.root)
        # Tracked companies with no DCF yet still belong on the index page.
        covered = {r.get("ticker") for r in ranked + unranked}
        extras = [{"ticker": t, "flags": ["NO_DCF"], "note": "no DCF model yet"}
                  for t in sorted(companies)
                  if t not in covered
                  and os.path.isdir(os.path.join(args.root, t, "Reports"))]
        write_html(ranked, unranked + extras, meta, load_scores(args.root),
                   companies, args.html)


if __name__ == "__main__":
    main()
