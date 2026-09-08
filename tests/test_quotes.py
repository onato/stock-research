"""scripts/quotes.py -- the one Yahoo client for the repo.

Four call sites used to each carry their own urllib block: screen.py,
prune_queue.py, dcf_context.py and the orchestrator's inline curl in
SKILL.md Step 8b/8c. They disagreed about timeouts, about the User-Agent,
and -- the one that mattered -- about whether `info.json:price_symbol`
redirects the quote. refresh_price ignored it, so BGI.NZ (renamed RTO.NZ,
frozen at $0.004) refreshed against the dead symbol.

The contract here: never raise into a caller. Every failure -- network,
HTTP, malformed JSON, a symbol Yahoo does not know -- returns None or an
empty list, because every caller's alternative to a quote is to leave the
stored number alone, and an exception escaping would abort a --all sweep
partway through.
"""

import json
import urllib.error
from pathlib import Path

import pytest
import quotes


def chart(meta=None, timestamps=None, closes=None):
    """A Yahoo v8 chart payload, as the API actually shapes it."""
    result = {"meta": meta or {}}
    if timestamps is not None:
        result["timestamp"] = timestamps
        result["indicators"] = {"quote": [{"close": closes}]}
    return json.dumps({"chart": {"result": [result], "error": None}})


@pytest.fixture
def fetched(monkeypatch):
    """Capture the URLs fetched, serving canned payloads in order."""
    calls = []

    def install(*payloads):
        queue = list(payloads)

        def fake(url, timeout=None):
            calls.append((url, timeout))
            item = queue.pop(0) if queue else ""
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(quotes, "_get", fake)
        return calls

    return install


class TestLive:
    def test_parses_price_currency_and_timestamp(self, fetched):
        fetched(chart({"regularMarketPrice": 4.82, "currency": "AUD",
                       "regularMarketTime": 1787699400}))
        q = quotes.live("TPW.AX")
        assert q.price == 4.82
        assert q.currency == "AUD"
        assert q.as_of.startswith("2026-")
        assert q.as_of.endswith("Z")

    def test_symbol_is_in_the_url(self, fetched):
        calls = fetched(chart({"regularMarketPrice": 1.0, "currency": "NZD"}))
        quotes.live("SEK.NZ")
        assert "SEK.NZ" in calls[0][0]

    def test_timeout_is_twenty_seconds(self, fetched):
        calls = fetched(chart({"regularMarketPrice": 1.0}))
        quotes.live("SEK.NZ")
        assert calls[0][1] == 20

    def test_missing_price_is_none(self, fetched):
        fetched(chart({"currency": "NZD"}))
        assert quotes.live("SEK.NZ") is None

    def test_network_failure_is_none_not_an_exception(self, fetched):
        fetched(OSError("connection reset"))
        assert quotes.live("SEK.NZ") is None

    def test_http_error_is_none(self, fetched):
        fetched(urllib.error.HTTPError("u", 429, "rate limited", {}, None))
        assert quotes.live("SEK.NZ") is None

    def test_malformed_json_is_none(self, fetched):
        fetched("not json at all")
        assert quotes.live("SEK.NZ") is None

    def test_yahoo_error_object_is_none(self, fetched):
        fetched(json.dumps({"chart": {"result": None,
                                      "error": {"code": "Not Found"}}}))
        assert quotes.live("SEK.NZ") is None

    def test_currency_absent_leaves_it_none(self, fetched):
        fetched(chart({"regularMarketPrice": 2.5}))
        q = quotes.live("SEK.NZ")
        assert q.price == 2.5
        assert q.currency is None


class TestPriceSymbolRouting:
    """`info.json:price_symbol` is the only redirect honoured.

    `aliases` deliberately is not: it lists former names and cross-listings,
    and quoting one of those silently reprices the ticker against a
    different listing (DOW.NZ's dormant NZX line at $0.00063 against an
    ASX-primary DCF put it top of the leaderboard at +26,884%).
    """

    def test_explicit_price_symbol_redirects(self, tmp_path):
        d = tmp_path / "BGI.NZ"
        d.mkdir()
        (d / "info.json").write_text(json.dumps({"price_symbol": "RTO.NZ"}))
        assert quotes.price_symbol(tmp_path, "BGI.NZ") == "RTO.NZ"

    def test_absent_info_json_uses_the_ticker(self, tmp_path):
        assert quotes.price_symbol(tmp_path, "SEK.NZ") == "SEK.NZ"

    def test_blank_price_symbol_uses_the_ticker(self, tmp_path):
        d = tmp_path / "SEK.NZ"
        d.mkdir()
        (d / "info.json").write_text(json.dumps({"price_symbol": "   "}))
        assert quotes.price_symbol(tmp_path, "SEK.NZ") == "SEK.NZ"

    def test_aliases_are_not_a_quote_source(self, tmp_path):
        d = tmp_path / "BGI.NZ"
        d.mkdir()
        (d / "info.json").write_text(json.dumps({"aliases": ["RTO.NZ"]}))
        assert quotes.price_symbol(tmp_path, "BGI.NZ") == "BGI.NZ"

    def test_corrupt_info_json_uses_the_ticker(self, tmp_path):
        d = tmp_path / "SEK.NZ"
        d.mkdir()
        (d / "info.json").write_text("{ not json")
        assert quotes.price_symbol(tmp_path, "SEK.NZ") == "SEK.NZ"

    def test_live_for_ticker_quotes_the_redirected_symbol(self, tmp_path, fetched):
        d = tmp_path / "BGI.NZ"
        d.mkdir()
        (d / "info.json").write_text(json.dumps({"price_symbol": "RTO.NZ"}))
        calls = fetched(chart({"regularMarketPrice": 0.119, "currency": "NZD"}))
        q = quotes.live_for_ticker("BGI.NZ", root=tmp_path)
        assert q.price == 0.119
        assert "RTO.NZ" in calls[0][0]
        assert "BGI.NZ" not in calls[0][0]


class TestMonthly:
    def test_parses_date_close_pairs(self, fetched):
        # 2016-01-01, 2016-02-01 UTC
        fetched(chart({}, [1451606400, 1454284800], [3.5, 3.75]))
        rows = quotes.monthly("SEK.NZ")
        assert [c for _, c in rows] == [3.5, 3.75]
        assert rows[0][0].year == 2016
        assert rows[0][0].month == 1
        assert rows[1][0].month == 2

    def test_null_closes_are_dropped(self, fetched):
        fetched(chart({}, [1451606400, 1454284800, 1456790400],
                      [3.5, None, 3.9]))
        assert [c for _, c in quotes.monthly("SEK.NZ")] == [3.5, 3.9]

    def test_range_and_interval_are_in_the_url(self, fetched):
        calls = fetched(chart({}, [1451606400], [3.5]))
        quotes.monthly("SEK.NZ", range_="10y")
        assert "range=10y" in calls[0][0]
        assert "interval=1mo" in calls[0][0]

    def test_failure_is_an_empty_list(self, fetched):
        fetched(OSError("boom"))
        assert quotes.monthly("SEK.NZ") == []

    def test_missing_indicators_is_an_empty_list(self, fetched):
        fetched(chart({"regularMarketPrice": 1.0}))
        assert quotes.monthly("SEK.NZ") == []

    def test_rows_are_sorted_oldest_first(self, fetched):
        fetched(chart({}, [1454284800, 1451606400], [3.75, 3.5]))
        rows = quotes.monthly("SEK.NZ")
        assert [d.month for d, _ in rows] == [1, 2]


class TestFx:
    def test_pair_returns_the_rate(self, fetched):
        fetched(chart({"regularMarketPrice": 1.0842, "currency": "USD"}))
        assert quotes.fx("EURUSD=X") == 1.0842

    def test_pair_is_url_encoded_in_the_request(self, fetched):
        calls = fetched(chart({"regularMarketPrice": 1.08}))
        quotes.fx("EURUSD=X")
        assert "EURUSD" in calls[0][0]

    def test_failure_is_none(self, fetched):
        fetched(OSError("boom"))
        assert quotes.fx("EURUSD=X") is None


class TestUserAgent:
    def test_request_carries_the_repo_user_agent(self, monkeypatch):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["ua"] = req.get_header("User-agent")
            seen["timeout"] = timeout
            raise OSError("stop here -- the header is what is under test")

        monkeypatch.setattr(quotes.urllib.request, "urlopen", fake_urlopen)
        assert quotes.live("SEK.NZ") is None
        assert "Mozilla/5.0" in seen["ua"]
        assert seen["timeout"] == 20


class TestNoCallerSeesAnException:
    """Belt and braces: the module is used inside --all sweeps over 163
    tickers, where one raised exception loses the other 162 results."""

    @pytest.mark.parametrize("boom", [
        OSError("dns"),
        urllib.error.HTTPError("u", 500, "server", {}, None),
        ValueError("weird"),
    ])
    def test_every_failure_mode_is_swallowed(self, fetched, boom):
        fetched(boom, boom, boom)
        assert quotes.live("X") is None
        assert quotes.monthly("X") == []
        assert quotes.fx("EURUSD=X") is None


def test_module_is_importable_from_scripts_dir():
    assert Path(quotes.__file__).name == "quotes.py"
