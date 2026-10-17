#!/usr/bin/env python3
"""The quality-growth list: companies whose revenue growth is still accelerating,
earned on high returns with little debt, ranked by upside to weighted IV.

A ticker is a *candidate* when its fiscal-year revenue growth rose three years
running (consecutive years, latest >= 10%, below 150% -- a bigger jump is a
merger or a units error). Each candidate is then graded on four tests:

  slowing    the latest interim's YoY growth is under 90% of the FY rate
  returns    ROIC (ROE for financials) below 15%, or unknown
  leverage   debt/equity above 0.5; negative equity passes only with net cash;
             financials are exempt
  valuation  price at or above weighted IV, or no IV

and lands in one tier:

  qualified  passes all four
  expensive  fails only on valuation -- the business qualifies, the price does not
  near_miss  fails exactly one non-valuation test (and may also be expensive)

Two kinds of acceleration are excluded outright and listed with the reason, so
the list never silently drops them: a recovery (growth was negative two years
ago, so the "acceleration" is a rebound) and a commodity-cyclical sector (the
growth is the metal or energy price). The quality-growth-analyst agent can
exclude more -- acquisition-driven or one-off growth -- through
`state/quality_growth_notes/{TICKER}.json`, the judgment files it owns.

ROIC comes from `build_dashboard.returns_on_capital`, the function the
dashboards and the index leaderboard use, so they cannot disagree -- except
that a blank TotalDebt is read as nil here (flagged `assumed_no_debt`), since
debt-free US filers (RDDT, ISRG, INCY) carry no borrowings tag at all.

Everything else is read from the warehouse (`make warehouse`). Output is
`state/quality_growth.json` (generated, committed); the portfolio tracker reads
it for its Quality Growth tab.

Usage:
  quality_growth.py                 # print the tiers
  quality_growth.py --write         # also write state/quality_growth.json
"""

import argparse
import datetime as dt
import json
import pathlib
import sys
from collections.abc import Callable
from typing import Any

import duckdb
import periods

REPO = pathlib.Path(__file__).resolve().parents[1]
WAREHOUSE = REPO / "state" / "research.duckdb"
OUT = REPO / "state" / "quality_growth.json"
NOTES = REPO / "state" / "quality_growth_notes"

MIN_GROWTH = 0.10
MAX_GROWTH = 1.5
MIN_RETURN_PCT = 15.0
MAX_DEBT_TO_EQUITY = 0.5
INTERIM_TOLERANCE = 0.9
REVIEW_MAX_AGE_DAYS = 30
TIER_ORDER = ("qualified", "expensive", "near_miss")

COMMODITY_WORDS = ("mining", "miner", "gold", "copper", "silver", "precious metals",
                   "metals", "steel", "iron ore", "lithium", "coal", "oil & gas",
                   "oil and gas", "exploration & production", "fertilizer", "fertiliser")

ReturnsLookup = Callable[[str], dict[str, Any] | None]


def acceleration(revenue: dict[int, float]) -> dict[str, Any] | None:
    """The last three consecutive FY growth rates, if each beat the one before."""
    years = sorted(y for y, r in revenue.items() if r and r > 0)
    if len(years) < 4:
        return None
    last = years[-4:]
    if last != list(range(last[0], last[0] + 4)):
        return None
    g = [revenue[last[i + 1]] / revenue[last[i]] - 1 for i in range(3)]
    if not (g[0] < g[1] < g[2]) or not (MIN_GROWTH <= g[2] < MAX_GROWTH):
        return None
    return {"fiscal_year": last[-1], "growth": g}


def interim_trend(fy_growth: float, interim_yoy: float | None) -> str:
    if interim_yoy is None:
        return "unknown"
    return "confirming" if interim_yoy >= fy_growth * INTERIM_TOLERANCE else "slowing"


def _leverage_ok(row: dict[str, Any]) -> bool:
    if row.get("financial"):
        return True
    equity = row.get("equity")
    debt = row.get("debt") or 0.0
    cash = row.get("cash") or 0.0
    if equity is None:
        return False
    if equity <= 0:
        return debt - cash <= 0
    return bool(debt / equity <= MAX_DEBT_TO_EQUITY)


def classify(row: dict[str, Any]) -> tuple[str | None, list[str]]:
    failures = []
    if row.get("interim_trend") == "slowing":
        failures.append("slowing")
    ret = row.get("return_pct")
    if ret is None or ret < MIN_RETURN_PCT:
        failures.append("returns")
    if not _leverage_ok(row):
        failures.append("leverage")
    up = row.get("upside_pct")
    if up is None or up <= 0:
        failures.append("valuation")
    others = [f for f in failures if f != "valuation"]
    if not failures:
        return "qualified", failures
    if not others:
        return "expensive", failures
    if len(others) == 1:
        return "near_miss", failures
    return None, failures


def exclusion(row: dict[str, Any]) -> str | None:
    if row["growth"][0] <= 0:
        return "recovery: FY growth was negative two years ago"
    sector = row.get("sector") or ""
    if any(w in sector.lower() for w in COMMODITY_WORDS):
        return f"commodity-cyclical sector: {sector}"
    return None


def needs_review(candidate: dict[str, Any], note: dict[str, Any] | None, today: dt.date) -> bool:
    if not note or not note.get("reviewed_at"):
        return True
    if note.get("tier_at_review") != candidate["tier"]:
        return True
    reviewed = dt.date.fromisoformat(str(note["reviewed_at"])[:10])
    return (today - reviewed).days > REVIEW_MAX_AGE_DAYS


def _revenue_by_ticker(con: duckdb.DuckDBPyConnection) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    for t, y, r in con.execute("""select ticker, fiscal_year, any_value(revenue) from metrics_m
            where is_annual and months = 12 and revenue > 0 group by 1, 2""").fetchall():
        out.setdefault(t, {})[y] = r
    return out


def _interim(con: duckdb.DuckDBPyConnection, ticker: str, after_fy: int) -> dict[str, Any] | None:
    """Latest same-period YoY from an interim in a fiscal year after the last FY."""
    rows = con.execute("""
        with i as (select period, period_type, fiscal_year, months, any_value(revenue) rev
                   from metrics_m where ticker = ? and not is_annual and revenue > 0
                   group by all)
        select a.period, a.rev / b.rev - 1 from i a join i b
          on a.period_type = b.period_type and a.months = b.months
         and b.fiscal_year = a.fiscal_year - 1
        where a.fiscal_year > ?
        order by a.fiscal_year desc,
                 case a.period_type when 'Q1' then 3 when 'H1' then 6 when 'Q2' then 6
                      when 'Q3' then 9 when '9M' then 9 else 12 end desc,
                 a.months desc
        limit 1""", [ticker, after_fy]).fetchall()
    return {"period": rows[0][0], "yoy": rows[0][1]} if rows else None


def _balance(con: duckdb.DuckDBPyConnection, ticker: str) -> tuple[Any, Any, Any]:
    row = con.execute("""select total_debt, cash_and_equivalents, shareholders_equity
        from metrics_m where ticker = ? and is_annual and months = 12
        order by fiscal_year desc limit 1""", [ticker]).fetchone()
    return (row[0], row[1], row[2]) if row else (None, None, None)


def _valuation(con: duckdb.DuckDBPyConnection, ticker: str) -> dict[str, Any]:
    d = con.execute("""select valuation_date, current_price, weighted_iv, sanity_passed, quote_currency
        from dcf where ticker = ?""", [ticker]).fetchone()
    if not d:
        return {}
    vdate, price, iv, sane, quote = d
    price_date = vdate
    p = con.execute("select date, close from prices where ticker = ? order by date desc limit 1",
                    [ticker]).fetchone()
    if p and p[1] and (not vdate or str(p[0]) > str(vdate)[:10]):
        price, price_date = p[1], str(p[0])
    entry = con.execute("""select entry_price from dcf_scenarios
        where ticker = ? and scenario = 'base'""", [ticker]).fetchone()
    return {"valuation_date": vdate, "sanity_passed": sane, "quote_currency": quote,
            "iv": iv, "price": price, "price_date": str(price_date)[:10] if price_date else None,
            "entry_price": entry[0] if entry else None,
            "upside_pct": (iv / price - 1) * 100 if iv and price else None}


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per company: a dual listing keeps its sane, freshest valuation."""
    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = (r.get("name") or r["ticker"]).strip().lower()
        rank = (r.get("sanity_passed") is not False, str(r.get("valuation_date") or ""))
        if key not in best or rank > best[key]["_rank"]:
            best[key] = {**r, "_rank": rank}
    return [{k: v for k, v in r.items() if k != "_rank"} for r in best.values()]


def build(con: duckdb.DuckDBPyConnection, returns: ReturnsLookup,
          notes: dict[str, dict[str, Any]], today: dt.date) -> dict[str, Any]:
    names = {t: (n, s) for t, n, s in con.execute("select ticker, name, sector from companies").fetchall()}
    rows: list[dict[str, Any]] = []
    for ticker, revenue in sorted(_revenue_by_ticker(con).items()):
        acc = acceleration(revenue)
        if acc is None or acc["fiscal_year"] < today.year - 1:
            continue
        g = acc["growth"]
        interim = _interim(con, ticker, acc["fiscal_year"])
        debt, cash, equity = _balance(con, ticker)
        ret = returns(ticker) or {}
        name, sector = names.get(ticker, (None, None))
        row: dict[str, Any] = {
            "ticker": ticker, "name": name, "sector": sector,
            "fiscal_year": acc["fiscal_year"], "growth": g,
            "growth_pct": [round(x * 100, 1) for x in g],
            "interim_trend": interim_trend(g[2], interim["yoy"] if interim else None),
            "return_measure": ret.get("measure"), "return_pct": ret.get("latest"),
            "return_avg3_pct": ret.get("avg3"), "financial": ret.get("measure") == "ROE",
            "assumed_no_debt": bool(ret.get("assumed_no_debt")),
            "debt": debt, "cash": cash, "equity": equity,
            "debt_to_equity": round(debt / equity, 2) if debt is not None and equity and equity > 0 else None,
            **_valuation(con, ticker),
        }
        row["interim"] = ({"period": interim["period"], "yoy_pct": round(interim["yoy"] * 100, 1),
                           "trend": row["interim_trend"]} if interim else None)
        rows.append(row)

    candidates, excluded = [], []
    for row in _dedupe(rows):
        note = notes.get(row["ticker"]) or {}
        reason = exclusion(row)
        if reason is None and note.get("exclude"):
            reason = f"analyst: {note.get('exclude_reason') or 'excluded'}"
        if reason:
            excluded.append({"ticker": row["ticker"], "reason": reason})
            continue
        tier, failures = classify(row)
        if tier is None:
            continue
        row.update(tier=tier, failures=failures, notes=note or None)
        for k in ("growth", "debt", "cash", "equity", "financial"):
            row.pop(k, None)
        candidates.append(row)

    candidates.sort(key=lambda c: (TIER_ORDER.index(c["tier"]), -(c.get("upside_pct") or -1e9)))
    for c in candidates:
        if c.get("upside_pct") is not None:
            c["upside_pct"] = round(c["upside_pct"], 1)
        if c.get("return_pct") is not None:
            c["return_pct"] = round(c["return_pct"], 1)
        if c.get("return_avg3_pct") is not None:
            c["return_avg3_pct"] = round(c["return_avg3_pct"], 1)
    return {
        "generated_at": today.isoformat(),
        "criteria": {"min_growth_pct": MIN_GROWTH * 100, "min_return_pct": MIN_RETURN_PCT,
                     "max_debt_to_equity": MAX_DEBT_TO_EQUITY,
                     "interim_tolerance": INTERIM_TOLERANCE},
        "candidates": candidates,
        "excluded": sorted(excluded, key=lambda e: e["ticker"]),
        "needs_review": [c["ticker"] for c in candidates
                         if needs_review(c, notes.get(c["ticker"]), today)],
    }


def fill_missing_debt(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], bool]:
    """Blank TotalDebt read as nil, so a debt-free filer (no borrowings tag at
    all) still gets a ROIC. The flag -- set only when the latest fiscal year is
    the blank one, since that is the headline ROIC -- travels to the output and
    the tab shows it: the assumption is visible, never silent."""
    def blank(r: dict[str, str]) -> bool:
        return not str(r.get("TotalDebt") or "").strip()

    annual = [r for r in rows if periods.is_annual(periods.parse(r.get("Period", "")))]
    latest = max(annual, key=lambda r: periods.sort_key(r.get("Period")), default=None)
    out = [{**r, "TotalDebt": "0"} if blank(r) else r for r in rows]
    return out, latest is not None and blank(latest)


def load_notes(folder: pathlib.Path) -> dict[str, dict[str, Any]]:
    """One judgment file per ticker, keyed by its filename stem."""
    if not folder.is_dir():
        return {}
    return {p.stem: json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))}


def _repo_returns(ticker: str) -> dict[str, Any] | None:
    import build_dashboard as BD
    import dashboard_spec
    path = REPO / "research" / ticker / "Reports" / f"{ticker}_Metrics.csv"
    try:
        _, rows = BD.read_csv(path.read_text())
    except OSError:
        return None
    rows, assumed = fill_missing_debt(rows)
    ret = BD.returns_on_capital(ticker, dashboard_spec.bucket_for(ticker), rows)
    ret["assumed_no_debt"] = assumed and ret.get("measure") == "ROIC"
    return ret


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help=f"write {OUT.relative_to(REPO)}")
    args = ap.parse_args(argv)
    notes = load_notes(NOTES)
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    out = build(con, _repo_returns, notes, dt.date.today())
    for tier in TIER_ORDER:
        rows = [c for c in out["candidates"] if c["tier"] == tier]
        print(f"\n{tier} ({len(rows)})")
        for c in rows:
            interim = c["interim"] or {}
            print(f"  {c['ticker']:<9} {str(c['name'])[:32]:<32} growth {c['growth_pct']} "
                  f"interim {interim.get('yoy_pct', '-')}  {c['return_measure']} {c['return_pct']}  "
                  f"D/E {c['debt_to_equity']}  upside {c['upside_pct']}  {','.join(c['failures'])}")
    print(f"\nexcluded {len(out['excluded'])}; needs review: {', '.join(out['needs_review']) or 'none'}")
    if args.write:
        OUT.write_text(json.dumps(out, indent=2, default=str) + "\n")
        print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
