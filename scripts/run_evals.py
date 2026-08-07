#!/usr/bin/env python3
"""Tier-1 eval: deterministic quality checks for one ticker's research output.

No LLM calls, stdlib only. Reads Reports/{TICKER}_Metrics.csv and _DCF.json
and runs three families of checks:

  * extraction integrity -- accounting identities (FCF = OCF - capex, margins,
    A = L + E), unit-drift/continuity, EPS-vs-sharecount scale, coverage
  * DCF internal consistency -- weights sum to 1, weighted IV recomputes from
    the scenario IVs, weighted upside matches price, sanity_check ran
  * pipeline health -- files exist and parse, dashboard references the CSV,
    extracted text non-trivial

Statuses: `fail` = the artifact is wrong; `warn` = suspicious but has known
legitimate causes (owner-FCF adjustments break the FCF identity by design;
NTA models have no SBC input); `skip` = inputs absent. The score counts only
pass/fail -- warns are a review queue, not a grade.

Scorecard goes to state/scores/{TICKER}_{date}.json with the agent-
prompt hash, so score changes are attributable to prompt versions.

Usage:
  run_evals.py TICKER [TICKER...]
  run_evals.py --all            # every ticker directory with a Reports/
  run_evals.py --strict ...     # exit 1 if any check fails (default exit 0)
"""

import csv
import datetime as dt
import itertools
import json
import sys
from collections.abc import Callable
from typing import Any

import dcf_fields as F
from schema import normalize

SCORES = F.REPO / "state" / "scores"

REL_TOL = 0.03          # identities: 3% relative slack for rounding
WIV_TOL = 0.02          # weighted-IV recompute: 2%
CONTINUITY_RATIO = 8    # adjacent-period jump that suggests unit drift
SHARES_RATIO = 3        # shares should be split-adjusted, so tighter
MIN_EXTRACT_BYTES = 200


def close(a: float, b: float, tol: float = REL_TOL) -> bool:
    scale = max(abs(a), abs(b), 1e-9)
    return abs(a - b) <= tol * scale


def margin_ok(m: float, part: float, whole: float) -> bool:
    """Margin column agrees with part/whole in percent (25) or fraction (0.25) form."""
    if abs(whole) < 1e-9:
        return True
    ratio = part / whole
    return abs(m - ratio * 100) <= 1.5 or abs(m - ratio) <= 0.015


def eps_ok(ni: float, eps: float, sh: float) -> bool:
    """EPS vs share count agree up to the units convention (shares in
    ones/thousands/millions); anything else is a units or split error."""
    if abs(eps) < 1e-12 or abs(sh) < 1e-9:
        return True
    implied = ni / eps
    return any(0.6 <= abs(implied / (sh * 1000 ** k)) <= 1.7
               for k in (-3, -2, -1, 0, 1, 2, 3))


class Card:
    def __init__(self) -> None:
        self.checks: list[dict[str, str]] = []

    def add(self, cid: str, status: str, detail: str = "") -> None:
        self.checks.append({"id": cid, "status": status, "detail": detail})

    def summary(self) -> dict[str, Any]:
        counts: dict[str, Any] = {s: sum(1 for c in self.checks if c["status"] == s)
                                  for s in ("pass", "warn", "fail", "skip")}
        graded = counts["pass"] + counts["fail"]
        counts["score"] = round(counts["pass"] / graded, 3) if graded else None
        return counts


# ---------------------------------------------------------------------------
# Metrics.csv checks
# ---------------------------------------------------------------------------

def load_metrics(ticker: str) -> tuple[list[dict[str, Any]] | None, Any]:
    path = F.REPO / "research" / ticker / "Reports" / f"{ticker}_Metrics.csv"
    if not path.exists():
        return None, None
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return [], []
    header = rows[0]
    cols = [normalize(h) for h in header]
    out: list[dict[str, Any]] = []
    for raw in rows[1:]:
        rec: dict[str, Any] = {}
        for i, cell in enumerate(raw[: len(cols)]):
            key = cols[i] or f"kpi:{header[i]}"
            rec[key] = cell.strip() if key == "period" else F.num(cell)
        out.append(rec)
    return out, header


def check_metrics(ticker: str, card: Card) -> None:
    rows, header = load_metrics(ticker)
    if rows is None:
        card.add("csv_parse", "fail", "Metrics.csv missing")
        return
    if not rows:
        card.add("csv_parse", "fail", "Metrics.csv empty")
        return
    card.add("csv_parse", "pass", f"{len(rows)} periods, {len(header)} columns")

    periods = [r.get("period") for r in rows]
    dupes = sorted({p for p in periods if p and periods.count(p) > 1})
    card.add("periods_unique", "fail" if dupes else "pass",
             f"duplicate periods: {dupes}" if dupes else "")

    def identity(cid: str, fields: tuple[str, ...],
                 test: Callable[..., bool], note: str = "") -> None:
        bad: list[Any]
        bad, n = [], 0
        for r in rows:
            vals = [r.get(f) for f in fields]
            if any(v is None for v in vals):
                continue
            n += 1
            if not test(*vals):
                bad.append(r.get("period"))
        if n == 0:
            card.add(cid, "skip", "fields not present")
        elif bad:
            card.add(cid, "warn", f"{len(bad)}/{n} periods off: {bad[:6]}{' ' + note if note else ''}")
        else:
            card.add(cid, "pass", f"{n} periods")

    # capex sign convention varies; owner-FCF adjustments (e.g. IFRS16 lease
    # principal) legitimately break this, hence warn not fail
    identity("identity_fcf",
             ("operating_cash_flow", "capex", "free_cash_flow"),
             lambda ocf, cx, fcf: close(ocf - cx, fcf) or close(ocf + cx, fcf),
             note="(owner-FCF adjustments are a known cause)")

    identity("identity_gross_margin",
             ("gross_margin", "gross_profit", "revenue"),
             margin_ok)
    identity("identity_net_margin",
             ("net_margin", "net_income", "revenue"),
             margin_ok)
    identity("identity_balance",
             ("total_assets", "total_liabilities", "shareholders_equity"),
             lambda a, liab, e: close(a, liab + e, 0.02))

    identity("eps_share_scale", ("net_income", "eps", "shares_outstanding"),
             eps_ok, note="(units/split mismatch)")

    def continuity(cid: str, field: str, limit: float) -> None:
        jumps: list[str]
        jumps, n = [], 0
        for prev, cur in itertools.pairwise(rows):
            a, b = prev.get(field), cur.get(field)
            if a is None or b is None or abs(a) < 1e-9 or (a < 0) != (b < 0):
                continue
            n += 1
            ratio = abs(b / a)
            if ratio > limit or ratio < 1 / limit:
                jumps.append(f"{prev.get('period')}->{cur.get('period')} {ratio:.1f}x")
        if n == 0:
            card.add(cid, "skip", "field not present")
        else:
            card.add(cid, "warn" if jumps else "pass",
                     "; ".join(jumps[:4]) if jumps else f"{n} transitions")

    continuity("continuity_revenue", "revenue", CONTINUITY_RATIO)
    continuity("continuity_equity", "shareholders_equity", CONTINUITY_RATIO)
    continuity("continuity_shares", "shares_outstanding", SHARES_RATIO)

    core = [c for c in {normalize(h) for h in header} if c and c not in ("period", "units", "currency")]
    if core:
        cells = sum(1 for r in rows for c in core if r.get(c) is not None)
        card.add("coverage", "pass",
                 f"{cells}/{len(rows) * len(core)} core cells filled "
                 f"({cells / (len(rows) * len(core)):.0%}), {len(core)} core columns mapped")
    else:
        card.add("coverage", "warn", "no headers map to core schema")


# ---------------------------------------------------------------------------
# DCF.json checks
# ---------------------------------------------------------------------------

def check_dcf(ticker: str, card: Card) -> None:
    dcf = F.load_dcf(ticker)
    if dcf is None:
        card.add("dcf_parse", "fail", "DCF.json missing or unparseable")
        return
    card.add("dcf_parse", "pass")

    price = F.num(dcf.get("current_price"))
    card.add("dcf_price", "pass" if price and price > 0 else "fail",
             f"current_price={price}")

    vdate = dcf.get("valuation_date")
    ok = False
    if isinstance(vdate, str):
        try:
            dt.date.fromisoformat(vdate)
            ok = True
        except ValueError:
            pass
    card.add("dcf_valuation_date", "pass" if ok else "warn", f"valuation_date={vdate!r}")

    ivs = F.scenario_ivs(dcf)
    missing = [s for s in F.SCENARIOS if s not in ivs]
    card.add("dcf_scenarios", "fail" if missing else "pass",
             f"missing scenario IV: {missing}" if missing else
             "bear/base/bull intrinsic values present")

    w = F.weights(dcf)
    if len(w) == 3 and abs(sum(w.values()) - 1.0) <= 0.01:
        card.add("dcf_weights", "pass", str(w))
    else:
        card.add("dcf_weights", "fail", f"weights={w}")

    # Recompute sum(w * IV) for every IV key name present in all three
    # scenarios (files carry parallel series: per-share vs whole-equity,
    # USD vs CAD) and match against every weighted_iv* the file declares.
    wivs = F.weighted_ivs(dcf)
    expected: dict[str, Any] = {}
    if len(w) == 3:
        names = set.intersection(*(set(ivs[s]) for s in F.SCENARIOS)) \
            if all(s in ivs for s in F.SCENARIOS) else set()
        expected = {n: sum(w[s] * ivs[s][n] for s in F.SCENARIOS) for n in names}
    if not wivs or not expected:
        card.add("dcf_weighted_iv", "skip", "not recomputable "
                 f"(candidates={list(wivs)}, groups={list(expected)})")
    else:
        def pool(k: str) -> list[Any]:
            same = [e for n, e in expected.items()
                    if F.currency_suffix(n) == F.currency_suffix(k)]
            return same or list(expected.values())

        matches = [k for k, v in wivs.items()
                   if any(close(v, e, WIV_TOL) for e in pool(k))]
        if matches:
            card.add("dcf_weighted_iv", "pass", f"recomputed match: {matches}")
        else:
            card.add("dcf_weighted_iv", "fail",
                     f"declared {wivs} vs recomputed {expected}")

    ups = F.weighted_upsides(dcf)
    if not (ups and price and wivs):
        card.add("dcf_weighted_upside", "skip")
    else:
        computed = [wiv / price - 1 for wiv in wivs.values()]
        ok = any(abs(u - c) <= 0.02 or abs(u - c * 100) <= 2.0
                 for u in ups.values() for c in computed)
        card.add("dcf_weighted_upside", "pass" if ok else "warn",
                 f"declared {ups} vs computed {[round(c, 3) for c in computed]}")

    # Shapes in the wild: {ran, passed}, {passed: true, ...}, {status: "PASSED"}
    sc = dcf.get("sanity_check")
    if not isinstance(sc, dict):
        card.add("dcf_sanity_check", "warn", "no sanity_check section")
    elif sc.get("ran") is False:
        card.add("dcf_sanity_check", "fail", "sanity_check.ran is false")
    else:
        passed = sc.get("passed")
        if passed is None and isinstance(sc.get("status"), str):
            passed = sc["status"].strip().upper() in ("PASSED", "PASS", "OK")
        if passed:
            card.add("dcf_sanity_check", "pass")
        elif passed is False and not (sc.get("fix_applied") or sc.get("trip_reasons")):
            card.add("dcf_sanity_check", "warn", "failed with no diagnosis/fix recorded")
        elif passed is False:
            card.add("dcf_sanity_check", "pass", "failed but diagnosed/fixed")
        else:
            card.add("dcf_sanity_check", "warn", f"no pass/fail verdict: {list(sc)[:5]}")

    high: list[str] = []
    for s, sc_ in F.scenario_block(dcf).items():
        if s in F.SCENARIOS and isinstance(sc_, dict):
            t = F.num(sc_.get("terminal_pct_of_value"))
            if t is not None:
                pct = t if t > 1.5 else t * 100
                if pct > 85:
                    high.append(f"{s}={pct:.0f}%")
    card.add("dcf_terminal_pct", "warn" if high else "pass",
             "terminal >85% of value: " + ", ".join(high) if high else "")

    eps = F.entry_prices(dcf)
    card.add("dcf_entry_price", "pass" if eps else "warn",
             "" if eps else "no numeric entry price found")

    inputs = dcf.get("inputs")
    has_sbc = isinstance(inputs, dict) and any(
        "sbc" in k.lower() or "stock_based" in k.lower() for k in inputs)
    card.add("policy_sbc", "pass" if has_sbc else "warn",
             "" if has_sbc else
             "inputs.sbc absent (fine for NTA/book models, a gap for FCF DCFs)")


# ---------------------------------------------------------------------------
# Pipeline health
# ---------------------------------------------------------------------------

def check_units_consistent(ticker: str, card: Card) -> None:
    """core_metrics and the exported CSV must use the same scale.

    XBRL originally wrote absolute dollars while the text path and every
    dashboard used millions, so running export_csv.py on a US ticker
    replaced 39000.97 with 39000966000.0 and silently broke the dashboard
    by 1e6. Both paths now write millions; this makes a regression loud.
    """
    reports = F.REPO / "research" / ticker / "Reports"
    db = reports / f"{ticker}.duckdb"
    csv_path = reports / f"{ticker}_Metrics.csv"
    if not db.exists() or not csv_path.exists():
        card.add("units_consistent", "skip", "no database or CSV")
        return

    try:
        import duckdb
        con = duckdb.connect(str(db), read_only=True)
        rows = con.execute(
            "SELECT period, revenue FROM core_metrics "
            "WHERE revenue IS NOT NULL AND period LIKE 'FY%'").fetchall()
        con.close()
    except Exception as e:
        card.add("units_consistent", "skip", f"db unreadable: {str(e)[:40]}")
        return
    if not rows:
        card.add("units_consistent", "skip", "no revenue in core_metrics")
        return
    db_rev = dict(rows)

    try:
        import csv as _csv
        with open(csv_path, newline="", errors="replace") as fh:
            csv_rev = {r["Period"]: r.get("Revenue")
                       for r in _csv.DictReader(fh) if r.get("Period")}
    except Exception as e:
        card.add("units_consistent", "skip", f"csv unreadable: {str(e)[:40]}")
        return

    for period, dbv in db_rev.items():
        raw = csv_rev.get(period)
        if raw in (None, ""):
            continue
        try:
            cv = float(str(raw).replace(",", ""))
        except ValueError:
            continue
        if cv == 0 or dbv == 0:
            continue
        ratio = dbv / cv
        # Same scale within rounding; anything near a power of 1000 apart
        # is a units mismatch rather than a data difference.
        if 0.99 <= ratio <= 1.01:
            card.add("units_consistent", "pass", f"{period} matches")
            return
        if ratio > 100 or ratio < 0.01:
            card.add("units_consistent", "fail",
                     f"{period}: db={dbv:,.1f} vs csv={cv:,.1f} "
                     f"({ratio:,.0f}x) -- units mismatch")
            return
    card.add("units_consistent", "warn", "no comparable FY period")


def check_health(ticker: str, card: Card) -> None:
    reports = F.REPO / "research" / ticker / "Reports"

    analysis = reports / f"{ticker}_Analysis.json"
    if not analysis.exists():
        card.add("analysis_present", "warn", "Analysis.json missing")
    else:
        try:
            json.loads(analysis.read_text())
            card.add("analysis_present", "pass")
        except json.JSONDecodeError as e:
            card.add("analysis_present", "fail", f"unparseable: {e}")

    # Dashboards mostly embed their data inline rather than fetch() the CSV,
    # so presence + non-trivial size is the deterministic check available.
    dash = reports / f"{ticker}_Dashboard.html"
    if not dash.exists():
        # fail, not warn: the dashboard is a deliverable, and AIR.NZ scored
        # 1.0 while missing one. A warn here let an incomplete run look
        # perfect.
        card.add("dashboard_present", "fail", "Dashboard.html missing")
    elif dash.stat().st_size < 5000:
        card.add("dashboard_present", "warn",
                 f"suspiciously small ({dash.stat().st_size} bytes)")
    else:
        card.add("dashboard_present", "pass")

    extracted = F.REPO / "research" / ticker / "Extracted"
    txts = sorted(extracted.glob("*.txt")) if extracted.is_dir() else []
    if not txts:
        card.add("extracted_nonempty", "skip", "no Extracted/*.txt")
    else:
        thin = [p.name for p in txts if p.stat().st_size < MIN_EXTRACT_BYTES]
        card.add("extracted_nonempty", "warn" if thin else "pass",
                 f"near-empty: {thin[:5]}" if thin else f"{len(txts)} files")


# ---------------------------------------------------------------------------

def evaluate(ticker: str) -> dict[str, Any]:
    card = Card()
    check_metrics(ticker, card)
    check_dcf(ticker, card)
    check_units_consistent(ticker, card)
    check_health(ticker, card)
    return {
        "ticker": ticker,
        "run_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agents_sha": F.agents_sha(),
        "git_head": F.git_head(),
        "checks": card.checks,
        "summary": card.summary(),
    }


def all_tickers() -> list[str]:
    return sorted(p.parent.name for p in F.REPO.glob("research/*/Reports")
                  if p.is_dir() and " " not in p.parent.name)


def main() -> int:
    argv = sys.argv[1:]
    strict = "--strict" in argv
    argv = [a for a in argv if a != "--strict"]
    if argv == ["--all"]:
        tickers = all_tickers()
    elif argv and not any(a.startswith("-") for a in argv):
        tickers = argv
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    SCORES.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    any_fail = False
    for t in tickers:
        result = evaluate(t)
        out = SCORES / f"{t}_{today}.json"
        out.write_text(json.dumps(result, indent=2) + "\n")
        s = result["summary"]
        fails = [c["id"] for c in result["checks"] if c["status"] == "fail"]
        warns = [c["id"] for c in result["checks"] if c["status"] == "warn"]
        any_fail |= bool(fails)
        line = (f"{t}: score={s['score']} pass={s['pass']} "
                f"warn={s['warn']} fail={s['fail']} skip={s['skip']}")
        if fails:
            line += f"  FAIL: {','.join(fails)}"
        if warns:
            line += f"  warn: {','.join(warns)}"
        print(line)
    return 1 if strict and any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
