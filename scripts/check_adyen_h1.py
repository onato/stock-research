#!/usr/bin/env python3
"""Poll Adyen IR for the H1-2026 shareholder letter and download it when it lands.

Not part of the deterministic pipeline under test -- this is an operational watcher
for a one-off event (13-Aug-2026 07:00 CEST). Exits 0 when the letter is found and
downloaded, 1 while still waiting, 2 on error.

Usage: python3 scripts/check_adyen_h1.py [--download]
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 research-bot"
INDEX = "https://investors.adyen.com/financials/_payload.json"
NEWS = "https://investors.adyen.com/news"
DEST = Path("research/ADYEY/PDFs/ADYEY_Letter_H1-2026.pdf")


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def discover_slug() -> str | None:
    """Find the real H1-2026 page slug from the financials index.

    Adyen broke its eight-year `h1-YYYY` convention for H1-2026 by appending a
    random suffix (`h1-2026-c2a1a`), so the slug must be discovered, never assumed.
    """
    try:
        raw = _get(INDEX).decode("utf-8", "replace")
    except Exception as exc:
        print(f"financials index not reachable: {exc}")
        return None
    slugs = re.findall(r"/financials/(h1-2026[a-z0-9\-]*)", raw)
    if not slugs:
        return None
    # Prefer a suffixed slug over a bare one; both may appear.
    return max(slugs, key=len)


def find_asset_urls() -> list[str]:
    """Return Frontify download URLs referenced by the H1-2026 payload."""
    slug = discover_slug()
    if not slug:
        print("H1-2026 not yet listed on the financials index.")
        return []
    url = f"https://investors.adyen.com/financials/{slug}/_payload.json"
    try:
        raw = _get(url).decode("utf-8", "replace")
    except Exception as exc:
        print(f"payload not reachable ({slug}): {exc}")
        return []
    urls = []
    marker = "brand.adyen.com/api/asset/"
    start = 0
    while (i := raw.find(marker, start)) != -1:
        lo = raw.rfind('"', 0, i) + 1
        hi = raw.find('"', i)
        if lo and hi != -1:
            u = raw[lo:hi]
            if u.startswith("http") and u not in urls:
                urls.append(u)
        start = i + len(marker)
    return urls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    args = ap.parse_args()

    urls = find_asset_urls()
    if not urls:
        print("H1-2026 letter NOT yet published (no payload / no assets).")
        return 1

    print(f"Found {len(urls)} asset URL(s):")
    for u in urls:
        print(f"  {u}")

    if args.download:
        DEST.parent.mkdir(parents=True, exist_ok=True)
        data = _get(urls[0], timeout=120)
        if not data.startswith(b"%PDF"):
            print(f"ERROR: fetched {len(data)} bytes but not a PDF")
            return 2
        DEST.write_bytes(data)
        print(f"Downloaded {len(data):,} bytes -> {DEST}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"error: {exc}")
        sys.exit(2)
