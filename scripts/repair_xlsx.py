#!/usr/bin/env python3
"""Repair hand-built .xlsx workbooks that Numbers refuses to open.

Two defects, both from the dcf-analyst writing workbooks by hand with the
system python3's openpyxl (no lxml) before `make build-dcf` existed:

1. docProps/core.xml with the namespaces declared on the child elements
   and no Dublin Core (dc:) element at all. Numbers rejects the file
   outright (WISE.L_DCF_Model.xlsx, 2026-09-22; 103 of 197 workbooks).
   With lxml the same library declares every namespace on the root and
   adds dc:creator, and Numbers opens it. The repair rewrites the part
   that way, keeping the created/modified dates.
2. A label beginning with '= ' (e.g. '= Owner FCF') stored as a formula,
   <f> Owner FCF</f>, because openpyxl treats any string starting with
   '=' as one. Excel repairs the cell silently; the cell is turned back
   into a string only when its body starts with whitespace AND still
   reads as prose once quoted strings and 'Sheet'! references are removed,
   so '= A2*2' stays the legal formula it is.

Both rewrites work on the XML inside the zip, so styles, shared strings,
column widths and every real formula are byte-for-byte untouched, and a
clean workbook is not rewritten. dcf_workbook.py never produces either
defect.

    python3 scripts/repair_xlsx.py research/*/Reports/*_DCF_Model.xlsx
    python3 scripts/repair_xlsx.py --check PATH...   # report only, exit 1 if any
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

CORE = "docProps/core.xml"
NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcmitype": "http://purl.org/dc/dcmitype/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
CELL = re.compile(r'<c\b([^>]*)><f(?: [^>]*)?>([^<]*)</f>(?:<v>[^<]*</v>|<v\s*/>)?</c>')
_QUOTED = re.compile(r"'[^']*'!|\"[^\"]*\"")
_PROSE = re.compile(r"[a-z]{2,}")


def is_text(formula: str) -> bool:
    """True when a stored formula body is really a label (see module doc)."""
    if not formula[:1].isspace():
        return False
    return bool(_PROSE.search(_QUOTED.sub("", formula)))


def _fix_sheet(xml: str) -> tuple[str, int]:
    n = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal n
        attrs, body = m.group(1), m.group(2)
        if not is_text(body):
            return m.group(0)
        n += 1
        attrs = re.sub(r'\s+t="[^"]*"', "", attrs)
        return f'<c{attrs} t="inlineStr"><is><t>={body}</t></is></c>'

    return CELL.sub(sub, xml), n


def _fix_core(xml: str) -> tuple[str, bool]:
    """Declare every namespace on the root and make sure a dc: element
    exists; returns the part unchanged when it already does."""
    start = xml.index("<cp:coreProperties")
    root_tag = xml[start:xml.index(">", start) + 1]
    if f'xmlns:dc="{NS["dc"]}"' in root_tag:
        return xml, False
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)
    root = ET.fromstring(xml)
    if root.find("dc:creator", NS) is None:
        creator = ET.Element(f"{{{NS['dc']}}}creator")
        creator.text = "openpyxl"
        root.insert(0, creator)
    return ET.tostring(root, encoding="unicode"), True


def _fixes(z: zipfile.ZipFile) -> dict[str, tuple[bytes, int]]:
    """Parts to rewrite -> (new bytes, number of defects fixed in it)."""
    out: dict[str, tuple[bytes, int]] = {}
    for name in z.namelist():
        if name == CORE:
            fixed, changed = _fix_core(z.read(name).decode("utf-8"))
            if changed:
                out[name] = (fixed.encode("utf-8"), 1)
        elif name.startswith("xl/worksheets/") and name.endswith(".xml"):
            fixed, n = _fix_sheet(z.read(name).decode("utf-8"))
            if n:
                out[name] = (fixed.encode("utf-8"), n)
    return out


def describe(path: Path) -> list[str]:
    """Human lines for each defect found, without writing."""
    with zipfile.ZipFile(path) as z:
        fixes = _fixes(z)
    lines = []
    if CORE in fixes:
        lines.append("core.xml lacks the dc: namespace (Numbers refuses it)")
    cells = sum(n for name, (_, n) in fixes.items() if name != CORE)
    if cells:
        lines.append(f"{cells} text-as-formula cell{'s' if cells != 1 else ''}")
    return lines


def repair(path: Path) -> int:
    """Rewrite the workbook in place; return the number of defects fixed.
    A clean workbook is left byte-for-byte as it was."""
    with zipfile.ZipFile(path) as src:
        fixes = _fixes(src)
        if not fixes:
            return 0
        with (tempfile.NamedTemporaryFile("wb", suffix=".xlsx", dir=path.parent, delete=False) as tmp,
              zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst):
            for info in src.infolist():
                dst.writestr(info, fixes[info.filename][0] if info.filename in fixes else src.read(info.filename))
    shutil.move(tmp.name, path)
    return sum(n for _, n in fixes.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--check", action="store_true", help="report only; exit 1 if any workbook needs repair")
    args = ap.parse_args(argv)
    found = 0
    for p in args.paths:
        lines = describe(p)
        if not lines:
            continue
        found += 1
        if not args.check:
            repair(p)
        print(f"{p}: {'has' if args.check else 'repaired'} " + "; ".join(lines))
    print(f"{found} of {len(args.paths)} workbook(s) {'need repair' if args.check else 'repaired'}")
    return 1 if (args.check and found) else 0


if __name__ == "__main__":
    sys.exit(main())
