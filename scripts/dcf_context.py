#!/usr/bin/env python3
"""Print everything the dcf-analyst needs before it models, in one call.

    python3 scripts/dcf_context.py TPW.AX

Sections: the live price (Yahoo, with its timestamp and 52-week range), the
ticker's memory line (the model decision, if one was made before), the
history pivot from metrics_normalized (millions of the reporting currency),
the kpis table, and the owner-FCF component lines grepped from the annual
filings with file:line pointers -- interest income, lease principal, SBC,
buybacks, D&A, capex, tax paid, diluted shares, dividends, NCI.

The agent used to spend half its turns rediscovering these; the numbers
themselves are still its call (units, which line is the right one).
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
import urllib.request
from dataclasses import dataclass

import periods

REPO = pathlib.Path(__file__).resolve().parents[1]
MEMORY_DIR = pathlib.Path.home() / ".claude" / "projects" / "-Users-swilliams-Stocks-Research" / "memory"

HISTORY_COLS = ["revenue", "ebitda", "net_income", "operating_cash_flow", "capex",
                "free_cash_flow", "stock_based_comp", "shareholders_equity",
                "cash_and_equivalents", "total_debt", "shares_outstanding", "eps"]

# Statement-line patterns. Each must be anchored at the label start so
# prose ("The Group's interest income policy ...") does not match, and a
# hit needs at least one number on the line.
COMPONENTS: list[tuple[str, re.Pattern[str]]] = [
    ("interest_income", re.compile(r"^\s*(?:less:\s*)?(?:interest|finance) (?:income|received)\b", re.IGNORECASE)),
    ("lease_principal", re.compile(r"^\s*(?:payment of |repayment of )?(?:the )?principal (?:portion|elements?) of lease"
                                   r"|^\s*repayments? of lease liabilit", re.IGNORECASE)),
    ("lease_interest", re.compile(r"^\s*interest (?:on|paid on) lease liabilit", re.IGNORECASE)),
    ("sbc", re.compile(r"^\s*(?:equity-settled |cash-settled )?share-based payments?(?: expense| transactions)?\b"
                       r"|^\s*stock-based compensation", re.IGNORECASE)),
    ("buybacks", re.compile(r"^\s*(?:payments? for )?(?:share|shares|stock) (?:buy-?back|repurchase)"
                            r"|^\s*repurchases? of (?:common|ordinary) (?:stock|shares)|^\s*purchase of treasury", re.IGNORECASE)),
    ("depreciation_amortisation", re.compile(r"^\s*(?:total )?depreciation and amorti[sz]ation\b", re.IGNORECASE)),
    ("capex_ppe", re.compile(r"^\s*(?:payments? for|purchases? of) (?:property, plant|plant and equipment)", re.IGNORECASE)),
    ("capex_intangibles", re.compile(r"^\s*(?:payments? for|purchases? of) intangible", re.IGNORECASE)),
    ("income_tax_paid", re.compile(r"^\s*income tax(?:es)? paid\b", re.IGNORECASE)),
    ("dividends_paid", re.compile(r"^\s*dividends? paid\b", re.IGNORECASE)),
    ("diluted_shares", re.compile(r"^\s*weighted average number of (?:ordinary )?shares.*diluted", re.IGNORECASE)),
    ("nci", re.compile(r"^\s*non-controlling interests?\b", re.IGNORECASE)),
]
NUM_RE = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?")
NOTE_REF_RE = re.compile(r"\(?\s*(?:refer(?:s)? to )?notes?\s+\d+[a-z]?\s*\)?", re.IGNORECASE)


@dataclass(frozen=True)
class Hit:
    name: str
    line_no: int
    line: str
    values: list[float]


@dataclass(frozen=True)
class Price:
    price: float
    currency: str
    as_of: str
    state: str
    high_52w: float | None
    low_52w: float | None


def _numbers(s: str) -> list[float]:
    """Numbers on a statement line, minus the note-reference column: a
    bare small integer before the amounts ("... 5   (6,615)  (6,798)")
    or a "(refer to note 20)" aside."""
    out = []
    toks = NUM_RE.findall(NOTE_REF_RE.sub(" ", s))
    if len(toks) > 1 and re.fullmatch(r"\d{1,2}", toks[0]):
        toks = toks[1:]
    for tok in toks:
        neg = tok.startswith("(") and tok.endswith(")")
        try:
            v = float(tok.strip("()").replace(",", ""))
        except ValueError:
            continue
        out.append(-v if neg else v)
    return out


def grep_components(text: str) -> list[Hit]:
    hits = []
    for i, ln in enumerate(text.split("\n"), 1):
        for name, rx in COMPONENTS:
            m = rx.search(ln)
            if not m:
                continue
            nums = _numbers(ln[m.end():])
            if nums:
                hits.append(Hit(name, i, re.sub(r"\s{2,}", "  ", ln.strip()), nums))
            break
    return hits


def render_history(rows: list[dict[str, object]], cols: list[str]) -> str:
    rows = sorted(rows, key=lambda r: periods.sort_key(str(r["period"])))
    w = max(10, *(len(c) for c in cols))
    head = f"{'period':12s}" + "".join(f"{c:>{w+2}s}" for c in cols)
    body = []
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c)
            if isinstance(v, (int, float)):
                cells.append(f"{v:>{w+2},.3f}".rstrip("0").rstrip("."))
            else:
                cells.append(f"{'-':>{w+2}s}")
        body.append(f"{r['period']!s:12s}" + "".join(cells))
    return "\n".join([head, *body])


def parse_price(payload: str) -> Price | None:
    try:
        m = json.loads(payload)["chart"]["result"][0]["meta"]
        ts = dt.datetime.fromtimestamp(int(m["regularMarketTime"]), dt.UTC)
        return Price(float(m["regularMarketPrice"]), str(m.get("currency")),
                     ts.isoformat().replace("+00:00", "Z"), str(m.get("marketState")),
                     m.get("fiftyTwoWeekHigh"), m.get("fiftyTwoWeekLow"))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None


def fetch_price(symbol: str) -> Price | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return parse_price(r.read().decode())
    except OSError:
        return None


def memory_line(ticker: str, memory_dir: pathlib.Path = MEMORY_DIR) -> str | None:
    f = memory_dir / "MEMORY.md"
    if not f.exists():
        return None
    needle = f"({ticker})"
    for ln in f.read_text().splitlines():
        if needle in ln:
            return ln.strip()
    return None


def _db_rows(db: pathlib.Path) -> tuple[list[dict[str, object]], list[tuple[object, ...]]]:
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    cols = ["period", *HISTORY_COLS]
    rows = [dict(zip(cols, r, strict=True))
            for r in con.execute(f"select {', '.join(cols)} from metrics_normalized").fetchall()]
    kpis = con.execute("select name, period, value, unit from kpis order by name, period").fetchall()
    con.close()
    return rows, kpis


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__.strip().split("\n")[0], file=sys.stderr)
        return 2
    ticker = args[0].upper()
    base = REPO / "research" / ticker
    info = json.loads((base / "info.json").read_text()) if (base / "info.json").exists() else {}
    print(f"# DCF context for {ticker}  ({info.get('name', '?')}; FY end {info.get('fiscal_year_end', '?')})")

    print("\n## Price (Yahoo)")
    p = fetch_price(info.get("price_symbol", ticker))
    if p:
        print(f"price={p.price} {p.currency} as_of={p.as_of} state={p.state} 52w={p.low_52w}-{p.high_52w}")
    else:
        print("unavailable -- fetch it yourself and record price_as_of")

    print("\n## Memory")
    print(memory_line(ticker) or "(no entry -- first valuation of this ticker; route via dcf-methods)")

    db = base / "Reports" / f"{ticker}.duckdb"
    if db.exists():
        rows, kpis = _db_rows(db)
        print("\n## History (metrics_normalized, millions of reporting currency; eps unscaled)")
        print(render_history(rows, HISTORY_COLS))
        print("\n## KPIs")
        for name, period, value, unit in sorted(kpis, key=lambda k: (str(k[0]), periods.sort_key(str(k[1])))):
            print(f"{name:28s} {period:10s} {value:>14,.6g} {unit or ''}")
    else:
        print(f"\n(no DuckDB at {db}; read the Metrics CSV)")

    print("\n## Component lines in annual filings (file:line -- units as printed)")
    for f in sorted((base / "Extracted").glob("*Annual*.txt"), key=lambda x: x.name):
        hits = grep_components(f.read_text(errors="replace"))
        if not hits:
            continue
        print(f"\n### {f.name}")
        seen: dict[str, int] = {}
        for h in hits:
            if seen.get(h.name, 0) >= 3:      # three per component per file is plenty
                continue
            seen[h.name] = seen.get(h.name, 0) + 1
            print(f"{h.name:26s} {h.line_no:6d}: {h.line[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
