"""prune_queue.py comments out delisted tickers before they cost a run.

The consequential rule: only a Yahoo 404 (or an explicit no-data answer) is
delisting evidence. Rate limits, 5xx and network failures say nothing about
the ticker, so they must never cause a live name to be commented out.
Nothing is deleted — entries are commented with the reason.
"""

import json
import sys
import urllib.error

import prune_queue
import pytest


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def patch_urlopen(monkeypatch, payload=None, exc=None):
    """Route urllib.request.urlopen to a canned payload or exception."""
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["ua"] = req.get_header("User-agent")
        if exc is not None:
            raise exc
        return FakeResponse(payload)

    monkeypatch.setattr(prune_queue.urllib.request, "urlopen", fake_urlopen)
    return seen


def chart(meta):
    return {"chart": {"result": [{"meta": meta}], "error": None}}


class TestQuote:
    def test_live_ticker_returns_price_and_currency(self, monkeypatch):
        seen = patch_urlopen(
            monkeypatch, chart({"regularMarketPrice": 4.05, "currency": "NZD"}))
        assert prune_queue.quote("AIR.NZ") == (4.05, "NZD")
        assert "AIR.NZ" in seen["url"]
        assert seen["ua"] == prune_queue.UA  # Yahoo rejects bare urllib UAs

    def test_404_is_delisting_evidence(self, monkeypatch):
        patch_urlopen(
            monkeypatch, exc=urllib.error.HTTPError("u", 404, "nf", None, None))
        assert prune_queue.quote("ACE.NZ") == (None, "no such symbol (404)")

    def test_rate_limit_is_not_delisting_evidence(self, monkeypatch):
        """429/5xx must read as fetch failures, which main() leaves in the
        queue — otherwise a rate-limited run comments out live names."""
        patch_urlopen(
            monkeypatch, exc=urllib.error.HTTPError("u", 429, "slow", None, None))
        assert prune_queue.quote("AIR.NZ") == (None, "fetch failed: HTTP 429")

    def test_network_error_is_a_fetch_failure(self, monkeypatch):
        patch_urlopen(monkeypatch, exc=OSError("connection reset"))
        px, info = prune_queue.quote("AIR.NZ")
        assert px is None
        assert info.startswith("fetch failed:")

    def test_failure_reason_truncated_to_40_chars(self, monkeypatch):
        patch_urlopen(monkeypatch, exc=OSError("x" * 100))
        _, info = prune_queue.quote("AIR.NZ")
        assert info == "fetch failed: " + "x" * 40

    def test_chart_error_dict_reports_its_code(self, monkeypatch):
        patch_urlopen(
            monkeypatch,
            {"chart": {"result": None, "error": {"code": "Not Found"}}})
        assert prune_queue.quote("X") == (None, "no data (Not Found)")

    def test_chart_error_non_dict_is_stringified(self, monkeypatch):
        patch_urlopen(monkeypatch, {"chart": {"result": None, "error": "gone"}})
        assert prune_queue.quote("X") == (None, "no data (gone)")

    def test_empty_result_list_is_no_data(self, monkeypatch):
        patch_urlopen(monkeypatch, {"chart": {"result": [], "error": None}})
        assert prune_queue.quote("X") == (None, "no data")

    def test_missing_price_in_meta(self, monkeypatch):
        patch_urlopen(monkeypatch, chart({"currency": "NZD"}))
        assert prune_queue.quote("X") == (None, "no price")

    def test_missing_currency_falls_back_to_question_mark(self, monkeypatch):
        patch_urlopen(monkeypatch, chart({"regularMarketPrice": 1.0}))
        assert prune_queue.quote("X") == (1.0, "?")


LIVE = (4.05, "NZD")
DEAD = (None, "no such symbol (404)")
FAILED = (None, "fetch failed: HTTP 429")


@pytest.fixture
def queue_dir(monkeypatch, tmp_path):
    """A tmp queue/ dir wired in as prune_queue.QUEUE, with sleep disabled."""
    qdir = tmp_path / "queue"
    qdir.mkdir()
    monkeypatch.setattr(prune_queue, "QUEUE", qdir)
    monkeypatch.setattr(prune_queue.time, "sleep", lambda s: None)
    return qdir


def run(monkeypatch, quotes, *argv):
    """Run main() with quotes scripted per ticker; an unscripted lookup
    raises KeyError, so tests prove which tickers were (not) queried."""
    monkeypatch.setattr(prune_queue, "quote", lambda t: quotes[t])
    monkeypatch.setattr(sys, "argv", ["prune_queue.py", *argv])
    return prune_queue.main()


class TestMain:
    def test_delisted_ticker_commented_with_reason(self, queue_dir, monkeypatch, capsys):
        f = queue_dir / "nzx.txt"
        f.write_text("ACE.NZ\nAIR.NZ\n")
        assert run(monkeypatch, {"ACE.NZ": DEAD, "AIR.NZ": LIVE}, "--apply") == 0
        assert f.read_text().splitlines() == [
            "# ACE.NZ  # delisted? no such symbol (404)",
            "AIR.NZ",
        ]
        out = capsys.readouterr().out
        assert "live: 1" in out
        assert "delisted: 1" in out
        assert "ACE.NZ" in out

    def test_fetch_failure_leaves_queue_untouched(self, queue_dir, monkeypatch, capsys):
        f = queue_dir / "nzx.txt"
        f.write_text("AIR.NZ\n")
        assert run(monkeypatch, {"AIR.NZ": FAILED}) == 0
        assert f.read_text() == "AIR.NZ\n"  # we did not learn anything
        out = capsys.readouterr().out
        assert "unresolved: 1" in out
        assert "could not check" in out

    def test_commented_and_blank_lines_never_queried(self, queue_dir, monkeypatch):
        # quotes covers only AIR.NZ: querying the commented ACE.NZ would
        # KeyError. This is what stops a pruned name being re-pruned forever.
        f = queue_dir / "nzx.txt"
        f.write_text("# ACE.NZ  # delisted? no such symbol (404)\n\nAIR.NZ\n")
        assert run(monkeypatch, {"AIR.NZ": LIVE}) == 0
        assert f.read_text() == "# ACE.NZ  # delisted? no such symbol (404)\n\nAIR.NZ\n"

    def test_inline_comment_stripped_before_lookup(self, queue_dir, monkeypatch):
        f = queue_dir / "nzx.txt"
        f.write_text("ACE.NZ  # renamed to BAI.NZ\n")
        run(monkeypatch, {"ACE.NZ": DEAD}, "--apply")
        assert f.read_text() == (
            "# ACE.NZ  # renamed to BAI.NZ  # delisted? no such symbol (404)\n")

    def test_default_is_a_dry_run_that_writes_nothing(self, queue_dir, monkeypatch, capsys):
        # Every other mutating script here is check-by-default; the queue
        # must not be rewritten by a bare `make queue-prune`.
        f = queue_dir / "nzx.txt"
        f.write_text("ACE.NZ\n")
        assert run(monkeypatch, {"ACE.NZ": DEAD}) == 0
        assert f.read_text() == "ACE.NZ\n"
        out = capsys.readouterr().out
        assert "delisted: 1" in out
        assert "--apply" in out

    def test_apply_writes_and_does_not_claim_a_dry_run(self, queue_dir, monkeypatch, capsys):
        f = queue_dir / "nzx.txt"
        f.write_text("ACE.NZ\n")
        assert run(monkeypatch, {"ACE.NZ": DEAD}, "--apply") == 0
        assert f.read_text() == "# ACE.NZ  # delisted? no such symbol (404)\n"
        assert "nothing written" not in capsys.readouterr().out

    def test_every_queue_file_is_checked(self, queue_dir, monkeypatch):
        (queue_dir / "asx.txt").write_text("BHP.AX\n")
        (queue_dir / "nzx.txt").write_text("AIR.NZ\n")
        assert run(monkeypatch, {"BHP.AX": LIVE, "AIR.NZ": DEAD}, "--apply") == 0
        assert (queue_dir / "asx.txt").read_text() == "BHP.AX\n"
        assert "# AIR.NZ" in (queue_dir / "nzx.txt").read_text()

    def test_file_flag_limits_the_run_to_one_file(self, queue_dir, monkeypatch, tmp_path):
        # nzx.txt sits in QUEUE but its ticker is unscripted: touching it
        # would KeyError, so passing --file must bypass the glob entirely.
        (queue_dir / "nzx.txt").write_text("AIR.NZ\n")
        target = tmp_path / "custom.txt"
        target.write_text("ACE.NZ\n")
        assert run(monkeypatch, {"ACE.NZ": DEAD}, "--apply", "--file", str(target)) == 0
        assert "# ACE.NZ" in target.read_text()

    def test_missing_file_is_skipped(self, queue_dir, monkeypatch, capsys):
        assert run(monkeypatch, {}, "--file", str(queue_dir / "absent.txt")) == 0
        assert "live: 0" in capsys.readouterr().out
