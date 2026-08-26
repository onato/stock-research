#!/usr/bin/env python3
"""How many DCF JSONs can the dashboard's slider engine reproduce?

    python3 tests/tools/dcf_engine_coverage.py [--verbose]

Runs the template's valuation engine (scripts/templates/dashboard.html) over
every research/*/Reports/*_DCF.json headlessly in node and reports, per
family, how many validate against valuation.*.intrinsic_value at the
scenario defaults. Diagnostic only -- reads live research/ files, so it is
not a test.
"""

import json
import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "scripts" / "templates" / "dashboard.html"


def engine_block() -> str:
    html = TEMPLATE.read_text()
    m = re.search(r"(function fmtIV\(v\).*?)\nfunction renderHeaderValuation\(\)", html, re.DOTALL)
    assert m, "engine block not found in template"
    return m.group(1)


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv
    block = engine_block()
    paths = sorted(REPO.glob("research/*/Reports/*_DCF.json"))
    js = "const results = {};\n"
    for p in paths:
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(d.get("assumptions"), dict) or not isinstance(d.get("valuation"), dict):
            continue
        t = p.parent.parent.name
        js += (f"(function(){{ const dcfData = {json.dumps(d)}; const dcfAnchors = {{}}; let currentScenario='base';\n"
               f"{block}\n try {{ validateEngines(); results[{json.dumps(t)}] = dcfEngineStatus; }}"
               f" catch (e) {{ results[{json.dumps(t)}] = {{error: String(e)}}; }} }})();\n")
    js += "console.log(JSON.stringify(results));"
    tmp = pathlib.Path(tempfile.mkdtemp()) / "coverage.js"
    tmp.write_text(js)
    r = subprocess.run(["node", str(tmp)], capture_output=True, text=True, check=False)
    if r.returncode:
        print(r.stderr, file=sys.stderr)
        return 1
    res = json.loads(r.stdout)
    fam: dict[str, list[int]] = {}
    for t, st in sorted(res.items()):
        if "error" in st:
            fam.setdefault("error", [0, 0])[1] += 1
            if verbose:
                print(f"{t:10s} ERROR {st['error'][:80]}")
            continue
        f = st["base"]["family"] or "none"
        ok = all(st[sc]["ok"] for sc in ("base", "bull", "bear"))
        fam.setdefault(f, [0, 0])
        fam[f][0] += ok
        fam[f][1] += 1
        if verbose or not ok:
            cells = " ".join(f"{sc}:{'ok' if st[sc]['ok'] else 'X'}"
                             f"({st[sc].get('iv') if st[sc].get('iv') is None else round(st[sc].get('iv'), 2)}"
                             f"/{st[sc].get('target')})" for sc in ("base", "bull", "bear"))
            print(f"{t:10s} {f:10s} {cells}")
    print()
    tot_ok = sum(v[0] for v in fam.values())
    tot = sum(v[1] for v in fam.values())
    for f, (ok, n) in sorted(fam.items()):
        print(f"{f:10s} {ok:3d}/{n}")
    print(f"{'TOTAL':10s} {tot_ok:3d}/{tot} validate at defaults")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
