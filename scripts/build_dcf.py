#!/usr/bin/env python3
"""Build {TICKER}_DCF.json and {TICKER}_DCF_Model.xlsx from {TICKER}_Drivers.json.

    python3 scripts/build_dcf.py TICKER            # make build-dcf TICKER=X
    python3 scripts/build_dcf.py TICKER --check    # make check-dcf TICKER=X
    python3 scripts/build_dcf.py --check-all       # every DCF in research/

The dcf-analyst writes the Drivers file (anchors, per-scenario driver paths,
weights, equity bridge, narrative) and runs this; it does not write the DCF
JSON or a model script itself. `--check` re-derives an existing DCF.json from
its own recorded assumptions and reports per-scenario agreement at the
dashboard's tolerance -- a hand-edited intrinsic_value, or one produced by a
model the assumptions do not describe, shows up as a mismatch. Exit 2 on a
malformed Drivers file (every problem named), 1 on a check mismatch.
"""

from __future__ import annotations

import csv
import json
import pathlib
import shutil
import sys
from typing import Any

import dcf_engine
import dcf_workbook
import periods

REPO = pathlib.Path(__file__).resolve().parents[1]
SCENARIO_ORDER = ("base", "bull", "bear")


def _reports(ticker: str) -> pathlib.Path:
    return REPO / "research" / ticker / "Reports"


def annual_history(csv_path: pathlib.Path) -> list[dict[str, Any]]:
    """The Metrics CSV's annual rows (Period first, numbers as floats) for the
    workbook's Actuals tab. Interims are left out: the tab is FY0 context."""
    if not csv_path.exists():
        return []
    out: list[dict[str, Any]] = []
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f):
            if not periods.is_annual(periods.parse(row.get("Period"))):
                continue
            rec: dict[str, Any] = {"Period": row["Period"]}
            for k, v in row.items():
                if k == "Period" or v is None:
                    continue
                try:
                    rec[k] = float(v) if v.strip() else None
                except ValueError:
                    rec[k] = v
            out.append(rec)
    return out


def _fmt_check(ticker: str, report: dict[str, str]) -> str:
    return f"{ticker}  " + " ".join(f"{sc}:{report[sc]}" for sc in SCENARIO_ORDER)


def build(ticker: str) -> int:
    reports = _reports(ticker)
    drivers_path = reports / f"{ticker}_Drivers.json"
    if not drivers_path.exists():
        print(f"no {drivers_path.name} at {reports}", file=sys.stderr)
        return 2
    try:
        drivers = json.loads(drivers_path.read_text())
        dcf = dcf_engine.build(drivers)
    except json.JSONDecodeError as e:
        print(f"{drivers_path.name}: invalid JSON: {e}", file=sys.stderr)
        return 2
    except dcf_engine.DriversError as e:
        print(f"{drivers_path.name} refused:", file=sys.stderr)
        for problem in str(e).split("; "):
            print(f"  - {problem}", file=sys.stderr)
        return 2

    out_json = reports / f"{ticker}_DCF.json"
    if out_json.exists():
        shutil.copy(out_json, reports / f"{ticker}_DCF.prev.json")
    out_json.write_text(json.dumps(dcf, indent=2, ensure_ascii=False) + "\n")
    out_xlsx = dcf_workbook.write(dcf, reports / f"{ticker}_DCF_Model.xlsx",
                                  history=annual_history(reports / f"{ticker}_Metrics.csv"))
    recalculated = dcf_workbook.recalc(out_xlsx)

    v, pw, ep = dcf["valuation"], dcf["probability_weighted"], dcf["entry_price"]
    quote = dcf["inputs"].get("quote_currency") or dcf["inputs"].get("currency") or ""
    print(f"{ticker}: {out_json.name} + {out_xlsx.name}"
          + ("" if recalculated else "  (values not cached: LibreOffice recalc unavailable)"))
    for sc in SCENARIO_ORDER:
        print(f"  {sc} {v[sc]['intrinsic_value']} {quote} (street {v[sc]['street_intrinsic_value']}, "
              f"TV {v[sc]['terminal_value_bound']}, entry {ep[sc]['entry_price']})")
    print(f"  weighted_iv {pw['weighted_iv']} {quote}  upside {pw['weighted_upside_pct']}%  "
          f"entry {ep['weighted_entry_price']}  band {pw['band_position']}")
    report = dcf_engine.check(dcf)
    print("  engine check: " + " ".join(f"{sc}:{report[sc]}" for sc in SCENARIO_ORDER))
    return 0


def check(ticker: str) -> int:
    path = _reports(ticker) / f"{ticker}_DCF.json"
    if not path.exists():
        print(f"no {path.name}", file=sys.stderr)
        return 2
    report = dcf_engine.check(json.loads(path.read_text()))
    print(_fmt_check(ticker, report))
    return 1 if any(r.startswith("mismatch") for r in report.values()) else 0


def check_all() -> int:
    counts: dict[str, int] = {"ok": 0, "mismatch": 0, "no-engine": 0}
    for path in sorted((REPO / "research").glob("*/Reports/*_DCF.json")):
        try:
            report = dcf_engine.check(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
        for r in report.values():
            counts[r.split()[0]] += 1
        if any(r.startswith("mismatch") for r in report.values()):
            print(_fmt_check(path.parent.parent.name, report))
    total = sum(counts.values())
    print(f"{total} scenarios: {counts['ok']} ok, {counts['mismatch']} mismatch, "
          f"{counts['no-engine']} no-engine (not the component family)")
    return 0


def main(argv: list[str]) -> int:
    if "--check-all" in argv:
        return check_all()
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        print(__doc__.strip().split("\n")[0], file=sys.stderr)
        return 2
    ticker = args[0].upper()
    if "--check" in argv:
        return check(ticker)
    return build(ticker)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
