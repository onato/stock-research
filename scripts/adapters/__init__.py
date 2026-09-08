"""Deterministic filing-download adapters, one module per source.

Each adapter separates the *plan* -- a pure function of a listing payload,
what is already held, and the year floor -- from the network I/O that
executes it. The plan is what the tests exercise and what `--dry-run`
prints; the I/O goes through module-level `get`/`download` callables so a
test can substitute them without patching subprocess.

Adapters emit named candidate files only. Nothing they download reaches
research/ without passing `filing_gate.check` -- an adapter never decides
that a payload is a real report.

Every network call carries an explicit `--max-time`, and every `fetch()`
takes a `deadline_s` budget checked between downloads. HKEX's
titleSearchServlet once hung and one ticker consumed five hours; a clean
`TimeoutError` is the contract that replaced it.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

# Per-call ceilings. A listing is small and must answer fast; a report PDF
# can legitimately be tens of megabytes.
LISTING_TIMEOUT = 30
HEAD_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 300
DEFAULT_DEADLINE = 600


class Deadline:
    """A wall-clock budget for one fetch() run.

    `check()` between downloads raises before starting work that cannot
    finish, so a stalled source costs the budget, not the night.
    """

    def __init__(self, seconds: float = DEFAULT_DEADLINE) -> None:
        self.seconds = seconds
        self.started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining(self) -> float:
        return self.seconds - self.elapsed

    def check(self, what: str = "") -> None:
        if self.remaining <= 0:
            detail = f" before {what}" if what else ""
            raise TimeoutError(
                f"deadline of {self.seconds:g}s exceeded after "
                f"{self.elapsed:.0f}s{detail}")


def curl_text(url: str, timeout: int = LISTING_TIMEOUT,
              headers: list[str] | None = None) -> str:
    """GET `url` as text, failing loudly on an HTTP error status."""
    cmd = ["curl", "-sfL", "--max-time", str(timeout), url, "-A", UA]
    for h in headers or []:
        cmd += ["-H", h]
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout + 10, check=True).stdout


def curl_head_size(url: str, timeout: int = HEAD_TIMEOUT) -> int:
    """Content-Length of `url` after redirects, or 0 if it does not say."""
    import re

    out = subprocess.run(
        ["curl", "-sIL", "--max-time", str(timeout), url, "-A", UA],
        capture_output=True, text=True, timeout=timeout + 10, check=False).stdout
    sizes = re.findall(r"content-length:\s*(\d+)", out, re.IGNORECASE)
    return int(sizes[-1]) if sizes else 0


def curl_download(url: str, out: Path, deadline: Deadline | None = None,
                  headers: list[str] | None = None) -> None:
    """Download `url` to `out`, never exceeding what is left of the deadline."""
    budget = DOWNLOAD_TIMEOUT
    if deadline is not None:
        budget = max(1, min(budget, int(deadline.remaining)))
    cmd = ["curl", "-sfL", "--max-time", str(budget), url, "-A", UA, "-o", str(out)]
    for h in headers or []:
        cmd += ["-H", h]
    subprocess.run(cmd, timeout=budget + 20, check=True)


def is_pdf(path: Path) -> bool:
    """Magic-byte check. Manifests, 403 pages and login walls are not reports."""
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False
