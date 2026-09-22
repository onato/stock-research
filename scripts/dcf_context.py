#!/usr/bin/env python3
"""Print everything the dcf-analyst needs before it models, in one call.

    python3 scripts/dcf_context.py TPW.AX

Sections: the live price (Yahoo, with its timestamp and 52-week range), the
ticker's memory line (the model decision, if one was made before), the
history pivot from metrics_normalized (millions of the reporting currency),
the kpis table, the owner-FCF component lines grepped from the annual
filings with file:line pointers -- interest income, lease principal, SBC,
buybacks, D&A, capex, tax paid, shares (on issue, weighted, diluted),
dividends, NCI, underlying earnings, net debt and its parts -- and the
outlook/guidance paragraphs of the latest annual, interim and presentation.

The agent used to spend half its turns rediscovering these; the numbers
themselves are still its call (units, which line is the right one). The
component list is what the dcf-analyst was measured grepping for (TPW.AX
2026-08-26, then the .AX batch of 2026-09-21: nine filing greps and five
sed windows per run, 72k chars back, all on the latest annual).
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
from dataclasses import dataclass

import periods
import quotes

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
    ("sbc", re.compile(r"^\s*(?:equity[- ]settled |cash[- ]settled )?(?:share|security|securities)-based payments?"
                       r"(?: expense| transactions)?\b|^\s*stock-based compensation", re.IGNORECASE)),
    ("buybacks", re.compile(r"^\s*(?:payments? for )?(?:share|shares|stock) (?:buy-?back|repurchase)"
                            r"|^\s*repurchases? of (?:common|ordinary) (?:stock|shares)|^\s*purchase of treasury", re.IGNORECASE)),
    ("depreciation_amortisation", re.compile(r"^\s*(?:total )?depreciation and amorti[sz]ation\b", re.IGNORECASE)),
    ("capex_ppe", re.compile(r"^\s*(?:payments? for|purchases? of) (?:property, plant|plant and equipment)", re.IGNORECASE)),
    ("capex_intangibles", re.compile(r"^\s*(?:payments? for|purchases? of) intangible", re.IGNORECASE)),
    ("income_tax_paid", re.compile(r"^\s*income tax(?:es)? paid\b", re.IGNORECASE)),
    ("dividends_paid", re.compile(r"^\s*(?:dividends?|distributions?) paid\b", re.IGNORECASE)),
    ("dps", re.compile(r"^\s*(?:dividends?|distributions?) per (?:ordinary )?(?:share|security)\b", re.IGNORECASE)),
    # Diluted before basic: the basic pattern matches the diluted line too.
    ("diluted_shares", re.compile(r"^\s*weighted average number of (?:ordinary )?(?:shares|securities).*diluted", re.IGNORECASE)),
    ("weighted_avg_shares", re.compile(r"^\s*weighted average number of (?:ordinary )?(?:shares|securities)\b", re.IGNORECASE)),
    ("shares_on_issue", re.compile(r"^\s*(?:number of )?(?:ordinary )?(?:shares|securities) on issue\b"
                                   r"|^\s*number of (?:ordinary )?(?:shares|securities)\b", re.IGNORECASE)),
    ("nci", re.compile(r"^\s*non-controlling interests?\b", re.IGNORECASE)),
    ("underlying_earnings", re.compile(r"^\s*underlying (?:ebitda|ebit|npat|net profit|profit|earnings)\b", re.IGNORECASE)),
    ("significant_items", re.compile(r"^\s*significant items\b", re.IGNORECASE)),
    ("net_debt", re.compile(r"^\s*(?:total )?net debt\b", re.IGNORECASE)),
    ("borrowings", re.compile(r"^\s*(?:total )?(?:borrowings|interest[- ]bearing (?:liabilities|loans and borrowings|debt)|bank loans)\b", re.IGNORECASE)),
    ("lease_liabilities", re.compile(r"^\s*(?:total )?lease liabilities\b", re.IGNORECASE)),
    ("interest_paid", re.compile(r"^\s*(?:interest|finance costs?) paid\b|^\s*net finance costs?\b", re.IGNORECASE)),
    ("income_tax_expense", re.compile(r"^\s*income tax (?:expense|benefit)\b|^\s*effective (?:income )?tax rate\b", re.IGNORECASE)),
]
PER_COMPONENT_PER_FILE = 3      # three lines per component per file is plenty
RECENT_ANNUALS = 3              # files that get every component; older ones get only...
HISTORY_COMPONENTS = ("interest_income", "lease_principal", "sbc", "buybacks", "dividends_paid")
HISTORY_PER_COMPONENT = 2       # ...the owner-FCF adjustments, for the history series

# Guidance: a window of lines around an outlook/guidance mention that also
# names a fiscal period and a figure. Climate-report "Guidance" and
# credit-rating "outlook Stable" carry neither.
GUIDANCE_KEY_RE = re.compile(r"\b(?:guidance|outlook)\b", re.IGNORECASE)
# Period tokens with the year in a group: FY27, FY2027, fiscal 2027, 1H27,
# H1 FY27, 2027. Two-digit years are 20xx.
GUIDANCE_PERIOD_RE = re.compile(r"\bFY ?(\d{4}|\d{2})\b|\bfiscal (?:year )?(20\d\d)\b"
                                r"|\b(?:1H|2H|H1|H2) ?(?:FY ?)?(\d{4}|\d{2})\b|\b(20[2-3]\d)\b")
GUIDANCE_FIGURE_RE = re.compile(r"[$€£¥]\s?[\d,]+|\d[\d,]*(?:\.\d+)?\s?(?:million|billion|m\b|bn\b|cents|%|per cent)", re.IGNORECASE)
NOISE_LINE_RE = re.compile(r"^\s*for personal use only\s*$|annual report\b.*\d+\s*$", re.IGNORECASE)
GUIDANCE_BEFORE, GUIDANCE_AFTER = 2, 8
GUIDANCE_HORIZON = 3            # years ahead; 2030/2050 targets are not guidance
MAX_GUIDANCE_WINDOWS = 8
MAX_GUIDANCE_LINES = 24
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


@dataclass(frozen=True)
class Window:
    line_no: int
    text: str


def _years(text: str) -> set[int]:
    out = set()
    for m in GUIDANCE_PERIOD_RE.finditer(text):
        tok = next(g for g in m.groups() if g)
        out.add(int(tok) if len(tok) == 4 else 2000 + int(tok))
    return out


def grep_guidance(text: str, after_year: int | None = None) -> list[Window]:
    """Outlook/guidance paragraphs, as the agent read them with sed after
    grepping. Overlapping windows merge, so a heading followed by the
    guidance sentence and the distribution sentence is one paragraph.

    Guidance names a period one to three years after the one being
    reported, so with the filing's fiscal year known, "in line with guidance"
    about the year just closed is dropped (on APA.AX FY2026 such
    retrospective mentions filled the window cap before the Outlook section
    was reached), and so is a 2030 emissions target (BHP.AX, AD.AS: ~11k
    chars of sustainability prose each)."""
    lines = text.split("\n")
    spans: list[list[int]] = []
    for i, ln in enumerate(lines):
        if not GUIDANCE_KEY_RE.search(ln):
            continue
        lo, hi = max(0, i - GUIDANCE_BEFORE), min(len(lines), i + GUIDANCE_AFTER + 1)
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = min(hi, spans[-1][0] + MAX_GUIDANCE_LINES)
        else:
            spans.append([lo, hi])
    out: list[Window] = []
    for lo, hi in spans:
        kept = [re.sub(r"\s{2,}", "  ", x.strip()) for x in lines[lo:hi]
                if x.strip() and not NOISE_LINE_RE.search(x)]
        body = "\n".join(kept)
        years = _years(body)
        forward = years if after_year is None else {
            y for y in years if after_year < y <= after_year + GUIDANCE_HORIZON}
        if forward and GUIDANCE_FIGURE_RE.search(body):
            out.append(Window(lo + 1, body))
        if len(out) >= MAX_GUIDANCE_WINDOWS:
            break
    return out


def _file_period(path: pathlib.Path) -> str:
    return path.stem.rsplit("_", 1)[-1]


def select_hits(hits: list[Hit], recent: bool) -> list[Hit]:
    """The lines worth printing from one annual file. A recent file gets up
    to three lines of every component; an older one only the owner-FCF
    adjustment lines the history series needs. APA.AX's nine annuals
    printed 31k chars before this budget (2026-09-22)."""
    cap = PER_COMPONENT_PER_FILE if recent else HISTORY_PER_COMPONENT
    seen: dict[str, int] = {}
    out = []
    for h in hits:
        if not recent and h.name not in HISTORY_COMPONENTS:
            continue
        if seen.get(h.name, 0) >= cap:
            continue
        seen[h.name] = seen.get(h.name, 0) + 1
        out.append(h)
    return out


def component_files(extracted: pathlib.Path) -> list[tuple[pathlib.Path, bool]]:
    """Annual filings oldest first, flagged recent for the latest few."""
    files = sorted(extracted.glob("*_Annual_*.txt"), key=lambda p: periods.sort_key(_file_period(p)))
    cutoff = max(0, len(files) - RECENT_ANNUALS)
    return [(p, i >= cutoff) for i, p in enumerate(files)]


def guidance_files(extracted: pathlib.Path) -> list[tuple[pathlib.Path, int | None]]:
    """The latest annual, latest interim and latest presentation, each with
    its fiscal year: guidance is restated in each, and older ones are
    superseded."""
    out = []
    for kinds in (("Annual",), ("HalfYear", "Quarterly"), ("Presentation",)):
        found = [p for k in kinds for p in extracted.glob(f"*_{k}_*.txt")]
        if found:
            latest = max(found, key=lambda p: periods.sort_key(_file_period(p)))
            out.append((latest, periods.parse(_file_period(latest)).fiscal_year))
    return out


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
    """Kept as a pure parser so the payload shape stays under test here;
    the fetching itself belongs to scripts/quotes.py."""
    try:
        m = json.loads(payload)["chart"]["result"][0]["meta"]
        ts = dt.datetime.fromtimestamp(int(m["regularMarketTime"]), dt.UTC)
        return Price(float(m["regularMarketPrice"]), str(m.get("currency")),
                     ts.isoformat().replace("+00:00", "Z"), str(m.get("marketState")),
                     m.get("fiftyTwoWeekHigh"), m.get("fiftyTwoWeekLow"))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None


def fetch_price(symbol: str) -> Price | None:
    """One Yahoo client for the repo (scripts/quotes.py). The private copy
    that used to live here disagreed with refresh_price about the
    price_symbol redirect, which is how a renamed ticker got quoted against
    its own corpse."""
    q = quotes.live(symbol)
    if q is None:
        return None
    return Price(q.price, str(q.currency), str(q.as_of), str(q.market_state),
                 q.high_52w, q.low_52w)


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

    print("\n## Component lines in annual filings (file:line -- units as printed;"
          f" latest {RECENT_ANNUALS} in full, older years owner-FCF adjustments only)")
    for f, recent in component_files(base / "Extracted"):
        hits = select_hits(grep_components(f.read_text(errors="replace")), recent)
        if not hits:
            continue
        print(f"\n### {f.name}")
        for h in hits:
            print(f"{h.name:26s} {h.line_no:6d}: {h.line[:110]}")

    print("\n## Outlook / guidance in the latest filings (file:line -- windows around each mention)")
    for f, fy in guidance_files(base / "Extracted"):
        wins = grep_guidance(f.read_text(errors="replace"), after_year=fy)
        if not wins:
            continue
        print(f"\n### {f.name}")
        for w in wins:
            print(f"--- line {w.line_no}")
            print(w.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
