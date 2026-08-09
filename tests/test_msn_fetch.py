"""msn_fetch.py pulls annual financials from MSN Money, cached on disk.

Why this source at all: the corpus's largest gap is CapEx (271 of 896 missing
cells), and the SEC XBRL path never populates it for non-US filers. MSN
publishes capitalExpenditures, operating cash flow and the rest per fiscal
year, and -- unlike the derived DuckDB in ~/Stocks/rule1 -- the raw API states
the currency of each statement explicitly. Spot-checking PINS and AIA.NZ
against the committed CSVs matched at a ratio of exactly 1.0000 on every
comparable cell, where the derived database disagreed on 17-22% of them.

Two rules the tests hold:

* Never trust the feed's currency blindly. BYD (1211.HK) comes back tagged
  BGN -- Bulgarian Lev -- for a company that reports in CNY. A ticker whose
  MSN currency contradicts the currency already recorded in its CSV is
  refused, not converted and not guessed.
* Never hit the network in tests, and never re-hit it in production when a
  cached copy is fresh. MSN rate limits, so the cache is the point.
"""

import json

import msn_fetch
import pytest


@pytest.fixture
def cache(tmp_path):
    d = tmp_path / "msn_cache"
    d.mkdir()
    return d


def statement(year, *, currency="USD", revenue=1e9, capex=-2e8, ocf=5e8,
              kind="annual"):
    return {
        "type": kind,
        "year": year,
        "incomeStatement": {
            "currency": currency,
            "revenue": {"totalRevenue": revenue,
                        "dilutedNormalizedEPS": 1.25},
            "income": {"incomeAvailableToComExclExtraOrd": 3e8,
                       "operatingIncome": 4e8},
        },
        "balanceSheets": {
            "currency": currency,
            "currentAssets": {"totalAssets": 9e9},
            "currentLiabilities": {"totalLiabilities": 4e9,
                                   "totalDebt": 1e9},
            "equity": {"totalEquity": 5e9},
        },
        "cashFlow": {
            "currency": currency,
            "operating": {"cashFromOperatingActivities": ocf},
            "investing": {"capitalExpenditures": capex},
        },
    }


class TestExtract:
    def test_annual_statements_become_periods(self):
        got = msn_fetch.extract([statement(2024), statement(2023)])
        assert sorted(got) == ["FY2023", "FY2024"]

    def test_quarterly_statements_are_ignored(self):
        # The CSVs carry quarters too, but MSN's quarter labelling does not
        # line up with the fiscal-quarter convention used elsewhere in the
        # repo; annual is the only period this claims to know.
        got = msn_fetch.extract([statement(2024), statement(2024, kind="q3")])
        assert list(got) == ["FY2024"]

    def test_values_are_scaled_to_millions(self):
        # The repo's canonical scale. MSN reports absolute currency units.
        got = msn_fetch.extract([statement(2024, revenue=3_646_166_000)])
        assert got["FY2024"]["Revenue"] == pytest.approx(3646.166)

    def test_capex_is_stored_as_reported_negative(self):
        # MSN reports capex as a negative investing outflow and the CSVs
        # follow the filing's own sign; flipping it here would double-count
        # against a FreeCashFlow that was derived the other way.
        got = msn_fetch.extract([statement(2024, capex=-937_800_000)])
        assert got["FY2024"]["CapEx"] == pytest.approx(-937.8)

    def test_free_cash_flow_is_derived_from_ocf_and_capex(self):
        got = msn_fetch.extract([statement(2024, ocf=474_300_000,
                                           capex=-937_800_000)])
        assert got["FY2024"]["FreeCashFlow"] == pytest.approx(-463.5)

    def test_per_share_figures_are_not_scaled(self):
        got = msn_fetch.extract([statement(2024)])
        assert got["FY2024"]["EPS"] == pytest.approx(1.25)

    def test_currency_is_carried_through(self):
        got = msn_fetch.extract([statement(2024, currency="NZD")])
        assert got["FY2024"]["Currency"] == "NZD"

    def test_a_missing_field_is_absent_not_zero(self):
        s = statement(2024)
        del s["cashFlow"]["investing"]["capitalExpenditures"]
        got = msn_fetch.extract([s])
        assert "CapEx" not in got["FY2024"]
        assert "FreeCashFlow" not in got["FY2024"]  # cannot derive without it

    def test_empty_payload_yields_nothing(self):
        assert msn_fetch.extract([]) == {}
        assert msn_fetch.extract(None) == {}

    def test_malformed_entries_are_skipped_not_fatal(self):
        # A feed this far outside our control gets defensive parsing: a
        # single junk record must not lose the whole ticker.
        payload = [
            "not a dict",
            {"type": "annual"},                       # no year
            {"type": "annual", "year": "not a year"},
            {"type": "annual", "year": 2024,
             "incomeStatement": {"revenue": {"totalRevenue": "n/a"}}},
            statement(2023),
        ]
        got = msn_fetch.extract(payload)
        assert list(got) == ["FY2023"]

    def test_a_shallow_statement_does_not_raise(self):
        # _dig walks paths that may stop being dicts partway down.
        got = msn_fetch.extract([{"type": "annual", "year": 2024,
                                  "cashFlow": "unexpectedly a string"}])
        assert got == {}


class TestCurrencyGuard:
    def test_matching_currency_is_accepted(self):
        rows = msn_fetch.extract([statement(2024, currency="NZD")])
        assert msn_fetch.currency_conflict(rows, "NZD") is None

    def test_conflicting_currency_is_reported(self):
        # BYD (1211.HK) really does come back tagged BGN. Converting on a
        # guess is exactly what the units convention forbids.
        rows = msn_fetch.extract([statement(2024, currency="BGN")])
        assert msn_fetch.currency_conflict(rows, "CNY") == ("BGN", "CNY")

    def test_no_recorded_currency_means_no_conflict(self):
        # Nothing to contradict; the caller decides whether to trust it.
        rows = msn_fetch.extract([statement(2024, currency="USD")])
        assert msn_fetch.currency_conflict(rows, "") is None

    def test_comparison_ignores_case_and_padding(self):
        rows = msn_fetch.extract([statement(2024, currency="usd")])
        assert msn_fetch.currency_conflict(rows, " USD ") is None


class TestCache:
    def test_a_miss_calls_the_fetcher_and_stores_the_payload(self, cache):
        calls = []

        def fake(ticker):
            calls.append(ticker)
            return [statement(2024)]

        got = msn_fetch.cached_payload("PINS", cache, fetch=fake)
        assert calls == ["PINS"]
        assert got == [statement(2024)]
        assert (cache / "PINS.json").is_file()

    def test_a_fresh_hit_does_not_call_the_fetcher(self, cache):
        msn_fetch.cached_payload("PINS", cache, fetch=lambda t: [statement(2024)])

        def boom(ticker):
            raise AssertionError("network hit on a fresh cache entry")

        got = msn_fetch.cached_payload("PINS", cache, fetch=boom)
        assert got == [statement(2024)]

    def test_a_stale_entry_is_refetched(self, cache):
        msn_fetch.cached_payload("PINS", cache, fetch=lambda t: [statement(2023)])
        path = cache / "PINS.json"
        blob = json.loads(path.read_text())
        blob["fetched_at"] = "2000-01-01T00:00:00"
        path.write_text(json.dumps(blob))

        got = msn_fetch.cached_payload("PINS", cache,
                                       fetch=lambda t: [statement(2024)],
                                       max_age_days=30)
        assert got == [statement(2024)]

    def test_max_age_zero_forces_a_refetch(self, cache):
        msn_fetch.cached_payload("PINS", cache, fetch=lambda t: [statement(2023)])
        got = msn_fetch.cached_payload("PINS", cache,
                                       fetch=lambda t: [statement(2024)],
                                       max_age_days=0)
        assert got == [statement(2024)]

    def test_a_ticker_with_a_slash_cannot_escape_the_cache_dir(self, cache):
        msn_fetch.cached_payload("../evil", cache, fetch=lambda t: [statement(2024)])
        assert not (cache.parent / "evil.json").exists()
        assert list(cache.glob("*.json"))

    def test_a_corrupt_cache_entry_is_refetched_not_fatal(self, cache):
        (cache / "PINS.json").write_text("{not json")
        got = msn_fetch.cached_payload("PINS", cache,
                                       fetch=lambda t: [statement(2024)])
        assert got == [statement(2024)]

    def test_a_failed_fetch_returns_none_and_writes_nothing(self, cache):
        assert msn_fetch.cached_payload("NOPE", cache, fetch=lambda t: None) is None
        assert not (cache / "NOPE.json").exists()

    def test_a_negative_cache_entry_is_honoured(self, cache):
        # MSN has no data for plenty of tickers. Re-asking every run wastes
        # the rate limit that the cache exists to protect.
        msn_fetch.cached_payload("NOPE", cache, fetch=lambda t: None,
                                 remember_misses=True)

        def boom(ticker):
            raise AssertionError("re-fetched a known miss")

        assert msn_fetch.cached_payload("NOPE", cache, fetch=boom,
                                        remember_misses=True) is None


class TestRateLimit:
    def test_consecutive_live_calls_are_spaced(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(msn_fetch.time, "sleep", slept.append)
        clock = iter([100.0, 100.0, 100.1, 100.1])
        monkeypatch.setattr(msn_fetch.time, "monotonic", lambda: next(clock))
        limiter = msn_fetch.RateLimiter(min_interval=1.5)
        limiter.wait()
        limiter.wait()
        assert len(slept) == 1
        assert slept[0] == pytest.approx(1.4, abs=0.05)

    def test_the_first_call_does_not_sleep(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(msn_fetch.time, "sleep", slept.append)
        monkeypatch.setattr(msn_fetch.time, "monotonic", lambda: 100.0)
        msn_fetch.RateLimiter(min_interval=1.5).wait()
        assert slept == []


class TestSecIdResolution:
    """The network layer, with transport stubbed -- tests never go online."""

    def test_a_ticker_resolves_to_its_sec_id(self, monkeypatch):
        seen = {}

        def fake(url, params, timeout=20):
            seen.update(params)
            return {"data": {"stocks": [json.dumps({"SecId": "bplhqh"})]}}

        monkeypatch.setattr(msn_fetch, "_get_json", fake)
        assert msn_fetch.sec_id("PINS") == "bplhqh"
        assert seen["query"] == "PINS"
        assert seen["market"] == "en-us"

    def test_the_listing_suffix_is_stripped_and_sets_the_market(self, monkeypatch):
        seen = {}

        def fake(url, params, timeout=20):
            seen.update(params)
            return {"data": {"stocks": [json.dumps({"SecId": "alqmec"})]}}

        monkeypatch.setattr(msn_fetch, "_get_json", fake)
        assert msn_fetch.sec_id("AIA.NZ") == "alqmec"
        assert seen["query"] == "AIA"      # MSN searches the bare symbol
        assert seen["market"] == "en-nz"

    def test_a_hardcoded_sec_id_skips_the_search(self, monkeypatch):
        def boom(url, params, timeout=20):
            raise AssertionError("searched for a ticker with a known SecId")

        monkeypatch.setattr(msn_fetch, "_get_json", boom)
        assert msn_fetch.sec_id("FLOW.AS") == "alo7kr"

    def test_no_match_yields_none(self, monkeypatch):
        monkeypatch.setattr(msn_fetch, "_get_json",
                            lambda *a, **k: {"data": {"stocks": []}})
        assert msn_fetch.sec_id("NOPE") is None

    def test_a_transport_error_yields_none(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("network down")

        monkeypatch.setattr(msn_fetch, "_get_json", boom)
        assert msn_fetch.sec_id("PINS") is None

    def test_the_rate_limiter_is_used_when_supplied(self, monkeypatch):
        waits = []
        monkeypatch.setattr(msn_fetch, "_get_json",
                            lambda *a, **k: {"data": {"stocks": []}})

        class Counting(msn_fetch.RateLimiter):
            def wait(self):
                waits.append(1)

        msn_fetch.sec_id("PINS", Counting())
        assert waits == [1]

    def test_a_dict_stock_entry_is_handled_like_a_json_string(self, monkeypatch):
        monkeypatch.setattr(
            msn_fetch, "_get_json",
            lambda *a, **k: {"data": {"stocks": [{"SecId": "abc123"}]}})
        assert msn_fetch.sec_id("PINS") == "abc123"


class TestFetch:
    def test_the_financials_call_carries_the_sec_id_filter(self, monkeypatch):
        calls = []

        def fake(url, params, timeout=20):
            calls.append((url, params))
            if "autosuggest" in url.lower() or "Query" in url:
                return {"data": {"stocks": [json.dumps({"SecId": "bplhqh"})]}}
            return [statement(2024)]

        monkeypatch.setattr(msn_fetch, "_get_json", fake)
        got = msn_fetch.fetch("PINS")
        assert got == [statement(2024)]
        financials = calls[-1][1]
        assert financials["$filter"] == "_p eq 'bplhqh'"
        assert financials["cm"] == "en-us"

    def test_an_unresolvable_ticker_never_calls_financials(self, monkeypatch):
        calls = []

        def fake(url, params, timeout=20):
            calls.append(url)
            return {"data": {"stocks": []}}

        monkeypatch.setattr(msn_fetch, "_get_json", fake)
        assert msn_fetch.fetch("NOPE") is None
        assert len(calls) == 1      # search only, no financials request

    def test_the_rate_limiter_spaces_both_calls(self, monkeypatch):
        waits = []

        def fake(url, params, timeout=20):
            if "Query" in url:
                return {"data": {"stocks": [json.dumps({"SecId": "x"})]}}
            return [statement(2024)]

        monkeypatch.setattr(msn_fetch, "_get_json", fake)

        class Counting(msn_fetch.RateLimiter):
            def wait(self):
                waits.append(1)

        msn_fetch.fetch("PINS", Counting())
        assert waits == [1, 1]      # search, then financials

    def test_a_failing_financials_call_yields_none(self, monkeypatch):
        def fake(url, params, timeout=20):
            if "Query" in url:
                return {"data": {"stocks": [json.dumps({"SecId": "x"})]}}
            raise OSError("500")

        monkeypatch.setattr(msn_fetch, "_get_json", fake)
        assert msn_fetch.fetch("PINS") is None


class TestMain:
    def _stub(self, monkeypatch, cache, payload):
        monkeypatch.setattr(msn_fetch, "fetch", lambda t, limiter=None: payload)
        monkeypatch.setattr(msn_fetch.time, "sleep", lambda s: None)

    def test_prints_periods_and_writes_json(self, monkeypatch, cache, capsys,
                                            tmp_path):
        self._stub(monkeypatch, cache, [statement(2024), statement(2023)])
        out = tmp_path / "msn.json"
        monkeypatch.setattr(
            "sys.argv",
            ["msn_fetch.py", "PINS", "--cache", str(cache),
             "--json", str(out)])
        assert msn_fetch.main() == 0
        printed = capsys.readouterr().out
        assert "2 annual period(s)" in printed
        assert "FY2024" in printed
        assert json.loads(out.read_text())["PINS"]["FY2024"]["Revenue"]

    def test_a_ticker_with_no_data_is_reported_not_fatal(self, monkeypatch,
                                                        cache, capsys):
        self._stub(monkeypatch, cache, None)
        monkeypatch.setattr("sys.argv",
                            ["msn_fetch.py", "NOPE", "--cache", str(cache)])
        assert msn_fetch.main() == 0
        assert "no annual data" in capsys.readouterr().err

    def test_refresh_bypasses_a_fresh_cache_entry(self, monkeypatch, cache,
                                                  capsys):
        msn_fetch.cached_payload("PINS", cache, fetch=lambda t: [statement(2020)])
        self._stub(monkeypatch, cache, [statement(2024)])
        monkeypatch.setattr("sys.argv",
                            ["msn_fetch.py", "PINS", "--cache", str(cache),
                             "--refresh"])
        assert msn_fetch.main() == 0
        assert "FY2024" in capsys.readouterr().out


class TestMarketCode:
    def test_suffix_selects_the_market(self):
        assert msn_fetch.market_for("AIA.NZ") == "en-nz"
        assert msn_fetch.market_for("WISE.L") == "en-gb"
        assert msn_fetch.market_for("1211.HK") == "zh-hk"
        assert msn_fetch.market_for("FLOW.AS") == "nl-nl"

    def test_bare_ticker_is_us(self):
        assert msn_fetch.market_for("PINS") == "en-us"

    def test_an_unknown_suffix_falls_back_to_us(self):
        assert msn_fetch.market_for("FOO.ZZ") == "en-us"
