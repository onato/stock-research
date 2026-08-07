#!/usr/bin/env python3
"""Route a ticker to the right extraction path, then report what happened.

Exchanges differ in what they publish, so one extractor cannot serve all
of them:

  US (bare symbol)  SEC XBRL companyfacts -- typed facts, exact periods
  everything else   text extraction from pdftotext output

This is the single entry point the skill calls; it picks the path, falls
back when the preferred one yields nothing, and logs a gap either way so
coverage problems accumulate somewhere visible instead of vanishing.

Usage:
  extract.py TICKER [--show]
  extract.py TICKER --path text     # force a path
"""

import argparse
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def is_us_symbol(ticker: str) -> bool:
    """SEC covers US filers only, and those carry no exchange suffix.

    A suffixed ticker (AGL.NZ, WISE.L, 0285.HK) is never in SEC's map, so
    there is no point paying the lookup.
    """
    return "." not in ticker


def run(script: str, *args: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                       capture_output=True, text=True, cwd=REPO, check=False)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def facts_count(ticker: str) -> tuple[int, int]:
    """Rows in whichever table the chosen path populated."""
    db = REPO / "research" / ticker / "Reports" / f"{ticker}.duckdb"
    if not db.exists():
        return 0, 0
    try:
        import duckdb
        con = duckdb.connect(str(db), read_only=True)
        f = con.execute("SELECT count(*) FROM facts").fetchone()[0]  # type: ignore[index]
        try:
            c = con.execute("SELECT count(*) FROM core_metrics").fetchone()[0]  # type: ignore[index]
        except Exception:
            c = 0
        con.close()
        return f, c
    except Exception:
        return 0, 0


def log_gap(ticker: str, kind: str, detail: str) -> None:
    subprocess.run([sys.executable, str(SCRIPTS / "log_gap.py"),
                    "--ticker", ticker, "--kind", kind, "--detail", detail],
                   capture_output=True, cwd=REPO, check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--path", choices=("auto", "xbrl", "text"), default="auto")
    args = ap.parse_args()
    t = args.ticker

    want = args.path
    if want == "auto":
        want = "xbrl" if is_us_symbol(t) else "text"

    if want == "xbrl":
        rc, out = run("build_facts_xbrl.py", t, *(["--show"] if args.show else []))
        print(out.rstrip())
        _, core = facts_count(t)
        if rc == 0 and core:
            print(f"  path: SEC XBRL  ({core} periods in core_metrics)")
            return 0
        # A US symbol SEC does not cover (foreign private issuer, recent
        # listing, ADR) still has PDFs on disk -- try those before giving up.
        print(f"  XBRL yielded nothing for {t}; falling back to text")
        log_gap(t, "other",
                "XBRL path returned no core_metrics; fell back to text extraction")
        want = "text"

    rc, out = run("build_facts.py", t, *(["--show"] if args.show else []))
    print(out.rstrip())
    facts, _ = facts_count(t)
    if facts:
        print(f"  path: text extraction  ({facts} candidate facts)")
        return 0

    # Neither path produced anything. The agent will read the filings
    # directly; record it so the regime shows up in `make gaps`.
    print(f"  NEITHER PATH produced facts for {t} -- agent must read filings")
    log_gap(t, "layout_unparsed",
            "neither XBRL nor text extraction produced facts; "
            "agent fell back to reading filings directly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
