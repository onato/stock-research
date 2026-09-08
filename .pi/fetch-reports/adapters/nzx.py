#!/usr/bin/env python3
"""Shim: the NZX adapter now lives in scripts/adapters/nzx.py (test-covered,
wired into `make fetch-filings`). fetch.sh still invokes this by path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from adapters.nzx import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
