#!/usr/bin/env python3
"""Per-ticker metadata: research/{TICKER}/info.json.

Curated by a strong model (or human) once, consumed by the fetcher every run.
Only "name" is semantically required; unknown fields are always preserved.
Writes are merge-preserving and atomic so a curator and the fetcher can't
destroy each other's fields.

CLI:
  company_info.py show TICKER
  company_info.py quirks TICKER          # combined source notes for the prompt
  company_info.py set TICKER key value   # curation helper (value parsed as JSON if possible)
"""
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def info_path(ticker: str) -> Path:
    return REPO / "research" / ticker / "info.json"


def load(ticker: str) -> dict:
    try:
        return json.loads(info_path(ticker).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write(ticker: str, updates: dict) -> dict:
    """Merge updates into info.json, preserving unknown fields. Atomic."""
    data = load(ticker)
    data.update(updates)
    data["updated_at"] = datetime.date.today().isoformat()
    path = info_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".info-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)
    return data


def quirks(ticker: str) -> str:
    """Combined source notes: curated info.json first, then quirks.json entry,
    then the exchange default."""
    notes = []
    info = load(ticker)
    if info.get("ir_url"):
        notes.append(f"Known investor-relations page (verified previously): {info['ir_url']}")
    if info.get("quirks"):
        notes.append(str(info["quirks"]))
    try:
        q = json.loads((HERE / "quirks.json").read_text())
    except (OSError, json.JSONDecodeError):
        q = {}
    if ticker in q:
        notes.append(q[ticker])
    if ticker.endswith(".NZ") and "_default_nz" in q:
        notes.append(q["_default_nz"].replace("{CODE}", ticker.split(".")[0]))
    if ticker.endswith(".L") and "_default_l" in q:
        notes.append(q["_default_l"])
    return " ".join(notes)


if __name__ == "__main__":
    cmd, ticker = sys.argv[1], sys.argv[2]
    if cmd == "show":
        print(json.dumps(load(ticker), indent=2, sort_keys=True))
    elif cmd == "quirks":
        print(quirks(ticker))
    elif cmd == "set":
        key, raw = sys.argv[3], sys.argv[4]
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        print(json.dumps(write(ticker, {key: value, "updated_by": "manual"}), indent=2))
    else:
        sys.exit(f"unknown command {cmd!r}")
