"""Deferred screen: push unlikely candidates to the back of the research queue.

The ethical screen says which companies Stephen will never hold. Nothing looked
at valuation or balance-sheet sanity before a research run, so a company on a
P/E of 150 or drowning in debt cost the same ~$6 as a plausible one, and came
up in file order. This screen reads the stockanalysis.com statistics page and
writes `state/deferred.txt`: the selector researches everything else first.

Three stances, each tested here:

* it DEFERS, it never excludes -- a deferred ticker is still researchable
  with `make run TICKER=X`, and comes up once the ordinary queue is done;
* missing data never defers (precision over recall, as in screen_ethics);
* banks, insurers and REITs are exempt from the debt rules, and when the
  sector is unknown the debt rules do not fire at all.
"""

import json
import pathlib

import pytest
import screen_deferred as sd
import select_ticker as st

FIX = pathlib.Path(__file__).parent / "fixtures" / "screener"
STATS_PAGE = (FIX / "stockanalysis_statistics_excerpt.html").read_text()
COMPANY_PAGE = (FIX / "stockanalysis_company_excerpt.html").read_text()


def stats(**over):
    """A clean, cheap, lightly geared company; override what the test needs."""
    base = {"pe": 15.0, "peForward": 13.0, "debtEquity": 0.4,
            "debtEbitda": 1.0, "interestCoverage": 12.0, "netcash": -100.0,
            "ebitda": 100.0, "debt": 200.0, "marketcap": 3.0e9,
            "netinc": 150.0, "equity": 500.0}
    base.update(over)
    return base


class TestUrls:
    @pytest.mark.parametrize(("ticker", "want"), [
        ("BHP.AX", "https://stockanalysis.com/quote/asx/BHP/statistics/"),
        ("0700.HK", "https://stockanalysis.com/quote/hkg/0700/statistics/"),
        ("AAPL", "https://stockanalysis.com/stocks/AAPL/statistics/"),
    ])
    def test_statistics_page_per_market(self, ticker, want):
        assert sd.stats_url(ticker) == want

    def test_company_page_is_the_profile_page(self):
        assert sd.company_url("BHP.AX") == \
            "https://stockanalysis.com/quote/asx/BHP/company/"

    def test_unmapped_market_has_no_url(self):
        # Guessing a path would bank a 404 as "no such company".
        assert sd.stats_url("X.ZZ") is None
        assert sd.company_url("X.ZZ") is None


class TestParseNumber:
    @pytest.mark.parametrize(("text", "want"), [
        ("21.76", 21.76),
        ("-14.26B", -14.26e9),
        ("308.94B", 308.94e9),
        ("1.5M", 1.5e6),
        ("12K", 12_000.0),
        ("2.1T", 2.1e12),
        ("24.00%", 24.0),
        ("7,958,276", 7_958_276.0),
        ("n/a", None),
        ("", None),
        ("-", None),
    ])
    def test_values_as_the_site_prints_them(self, text, want):
        assert sd.parse_number(text) == want


class TestParseStatistics:
    def test_reads_every_field_from_the_embedded_literals(self):
        got = sd.parse_statistics(STATS_PAGE)
        assert got["pe"] == 21.76
        assert got["peForward"] == 16.83
        assert got["debtEquity"] == 0.50
        assert got["debtEbitda"] == 0.92
        assert got["interestCoverage"] == 28.27
        assert got["netcash"] == -14.26e9
        assert got["ebitda"] == 43.22e9
        assert got["marketcap"] == 308.94e9
        assert got["netinc"] == 14.21e9

    def test_every_field_is_present_even_when_absent_from_the_page(self):
        got = sd.parse_statistics("<html><body>nothing here</body></html>")
        assert set(got) == set(sd.FIELDS)
        assert all(v is None for v in got.values())

    def test_n_a_is_none_not_zero(self):
        page = '{id:"pe",title:"PE Ratio",value:"n/a",hover:"n/a"}'
        assert sd.parse_statistics(page)["pe"] is None

    def test_empty_page_is_not_a_company(self):
        # A 200 with no literals at all is a template page, not data.
        assert sd.parse_statistics("") == dict.fromkeys(sd.FIELDS)
        assert not sd.has_data(sd.parse_statistics(""))
        assert sd.has_data(sd.parse_statistics(STATS_PAGE))


class TestParseSector:
    def test_reads_the_sector_literal(self):
        assert sd.parse_sector(COMPANY_PAGE) == "Materials"

    def test_no_sector_is_none(self):
        assert sd.parse_sector("<html></html>") is None


class TestIsFinancial:
    @pytest.mark.parametrize("sector", [
        "Financials", "Real Estate", "Banks", "Insurance",
        "Wealth Management / Superannuation / Banking",
        "Property / REIT", "Deposit Taker / Lending", "Asset Management",
    ])
    def test_financials_and_property_are_exempt(self, sector):
        assert sd.is_financial(sector)

    @pytest.mark.parametrize("sector", [
        "Industrials", "Materials", "Technology", "Airports / Infrastructure",
        "Consumer Staples", "", None,
    ])
    def test_everything_else_is_not(self, sector):
        assert not sd.is_financial(sector)


class TestEvaluate:
    def test_a_clean_company_is_not_deferred(self):
        s = sd.evaluate(stats(), "Industrials")
        assert s.reasons == ()
        assert not s.deferred

    def test_a_ridiculous_pe_defers(self):
        s = sd.evaluate(stats(pe=73.2), "Industrials")
        assert s.reasons == ("P/E 73.2",)

    def test_pe_at_the_threshold_does_not(self):
        assert not sd.evaluate(stats(pe=50.0), "Industrials").deferred
        assert sd.evaluate(stats(pe=50.1), "Industrials").deferred

    def test_a_loss_maker_is_not_deferred_on_pe(self):
        # No earnings is a growth story or a trough, not a ridiculous price.
        assert not sd.evaluate(stats(pe=None, netinc=-50.0), "Technology").deferred

    def test_pe_rule_ignores_sector(self):
        assert sd.evaluate(stats(pe=99.0), "Financials").reasons == ("P/E 99.0",)

    def test_too_much_debt_defers_an_industrial(self):
        s = sd.evaluate(stats(debtEquity=2.9), "Industrials")
        assert s.reasons == ("D/E 2.9x",)

    def test_debt_rules_exempt_financials(self):
        for sector in ("Financials", "Real Estate", "Banks / Lending"):
            s = sd.evaluate(stats(debtEquity=9.0, interestCoverage=0.5,
                                  netcash=-2000.0), sector)
            assert s.reasons == (), sector

    def test_debt_rules_do_not_fire_on_an_unknown_sector(self):
        # A bank with no sector text looks like a bankrupt industrial.
        s = sd.evaluate(stats(debtEquity=9.0), None)
        assert s.reasons == ()
        assert s.sector is None

    def test_net_debt_to_ebitda_is_computed_from_net_cash(self):
        # net cash -610 on EBITDA 100 = 6.1x, even though gross debt/EBITDA
        # (the site's own figure) is 2.0.
        s = sd.evaluate(stats(netcash=-610.0, ebitda=100.0, debtEbitda=2.0),
                        "Industrials")
        assert s.net_debt_ebitda == pytest.approx(6.1)
        assert s.reasons == ("ND/EBITDA 6.1x",)

    def test_net_cash_beats_gross_debt(self):
        # A company with more cash than debt is not over-geared, whatever the
        # gross Debt/EBITDA says.
        s = sd.evaluate(stats(netcash=500.0, ebitda=100.0, debtEbitda=6.0),
                        "Industrials")
        assert s.net_debt_ebitda == pytest.approx(-5.0)
        assert s.reasons == ()

    def test_falls_back_to_the_sites_gross_ratio(self):
        s = sd.evaluate(stats(netcash=None, ebitda=None, debtEbitda=6.0),
                        "Industrials")
        assert s.net_debt_ebitda == 6.0
        assert s.reasons == ("ND/EBITDA 6.0x",)

    def test_negative_ebitda_gives_no_ratio(self):
        s = sd.evaluate(stats(netcash=-500.0, ebitda=-10.0, debtEbitda=None),
                        "Industrials")
        assert s.net_debt_ebitda is None
        assert s.reasons == ()

    def test_thin_interest_cover_defers(self):
        s = sd.evaluate(stats(interestCoverage=1.4), "Industrials")
        assert s.reasons == ("int cover 1.4x",)

    def test_unknown_interest_cover_does_not(self):
        assert not sd.evaluate(stats(interestCoverage=None), "Industrials").deferred

    def test_negative_interest_cover_is_a_loss_maker_not_a_debt_problem(self):
        # CXO.AX (2026-09-22 live run): no debt at all, EBIT negative, so the
        # site prints cover -21.6x. That is the loss-maker case, which the
        # P/E rule already declines to defer; the debt rule must agree.
        s = sd.evaluate(stats(interestCoverage=-21.6, debtEquity=0.0),
                        "Materials")
        assert s.reasons == ()

    def test_negative_equity_does_not_fire_de(self):
        # Buyback-driven negative equity (AutoZone, Home Depot) shows as a
        # negative D/E; that is not "too much debt".
        s = sd.evaluate(stats(debtEquity=-3.5), "Consumer Discretionary")
        assert s.reasons == ()

    def test_several_reasons_are_all_recorded(self):
        s = sd.evaluate(stats(pe=80.0, debtEquity=3.0, interestCoverage=1.0),
                        "Industrials")
        assert s.reasons == ("P/E 80.0", "D/E 3.0x", "int cover 1.0x")

    def test_debt_reasons_alone_say_whether_the_sector_matters(self):
        # The crawler uses this to decide whether to spend a second request
        # on the company page.
        assert sd.debt_reasons(stats(debtEquity=3.0))
        assert not sd.debt_reasons(stats())


class TestWriteInfo:
    def test_writes_the_screen_block_and_keeps_everything_else(self, tmp_path):
        p = tmp_path / "info.json"
        p.write_text(json.dumps({"name": "Acme", "ethics": {"flags": []}}))
        sd.write_info(p, sd.evaluate(stats(pe=73.2), "Industrials"))
        d = json.loads(p.read_text())
        assert d["name"] == "Acme"
        assert d["ethics"] == {"flags": []}
        assert d["screen"]["pe"] == 73.2
        assert d["screen"]["defer"] == ["P/E 73.2"]
        assert d["screen"]["source"] == "stockanalysis.com"
        assert d["screen"]["fetched_at"]

    def test_creates_a_missing_info(self, tmp_path):
        p = tmp_path / "research" / "NEW" / "info.json"
        sd.write_info(p, sd.evaluate(stats(), "Industrials"))
        assert json.loads(p.read_text())["screen"]["defer"] == []

    def test_fills_an_empty_sector_only(self, tmp_path):
        p = tmp_path / "info.json"
        p.write_text(json.dumps({"sector": "Airports / Infrastructure"}))
        sd.write_info(p, sd.evaluate(stats(), "Industrials"))
        assert json.loads(p.read_text())["sector"] == "Airports / Infrastructure"
        p.write_text(json.dumps({}))
        sd.write_info(p, sd.evaluate(stats(), "Industrials"))
        assert json.loads(p.read_text())["sector"] == "Industrials"


class TestDeferredFile:
    def _info(self, root, ticker, defer, **extra):
        d = root / "research" / ticker
        d.mkdir(parents=True, exist_ok=True)
        (d / "info.json").write_text(json.dumps(
            {"screen": {"defer": defer, **extra}}))

    def test_lists_only_deferred_tickers_sorted(self, tmp_path):
        self._info(tmp_path, "ZZZ", ["P/E 80.0"])
        self._info(tmp_path, "AAA", ["D/E 3.0x", "int cover 1.0x"])
        self._info(tmp_path, "OK", [])
        n = sd.write_deferred(tmp_path)
        assert n == 2
        body = (tmp_path / "state" / "deferred.txt").read_text()
        lines = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
        assert lines == ["AAA  D/E 3.0x; int cover 1.0x", "ZZZ  P/E 80.0"]

    def test_the_selector_reads_it_back(self, tmp_path):
        # Same `TICKER  reason` grammar as never_interested.txt, so the same
        # reader parses both.
        self._info(tmp_path, "ZZZ", ["P/E 80.0"])
        self._info(tmp_path, "OK", [])
        sd.write_deferred(tmp_path)
        assert st._ticker_file(tmp_path / "state" / "deferred.txt") == {"ZZZ"}

    def test_header_says_it_is_generated(self, tmp_path):
        self._info(tmp_path, "ZZZ", ["P/E 80.0"])
        sd.write_deferred(tmp_path)
        head = (tmp_path / "state" / "deferred.txt").read_text().splitlines()[0]
        assert "GENERATED" in head

    def test_an_unparseable_info_is_ignored(self, tmp_path):
        d = tmp_path / "research" / "ODD"
        d.mkdir(parents=True)
        (d / "info.json").write_text("{not json")
        assert sd.write_deferred(tmp_path) == 0


class TestCandidates:
    def _queue(self, root, name, tickers):
        (root / "queue").mkdir(exist_ok=True)
        (root / "queue" / name).write_text("\n".join(tickers) + "\n")

    def test_unresearched_queued_tickers_only(self, tmp_path):
        self._queue(tmp_path, "us.txt", ["NEW", "DONE", "# GONE  # delisted"])
        d = tmp_path / "research" / "DONE" / "Reports"
        d.mkdir(parents=True)
        (d / "DONE_Metrics.csv").write_text("Period\n")
        assert sd.candidates(tmp_path) == ["NEW"]

    def test_never_interested_is_not_worth_a_fetch(self, tmp_path):
        self._queue(tmp_path, "us.txt", ["LMT", "NEW"])
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "never_interested.txt").write_text("LMT  weapons\n")
        assert sd.candidates(tmp_path) == ["NEW"]

    def test_portfolio_and_watchlist_are_never_deferred(self, tmp_path):
        self._queue(tmp_path, "priority.txt", ["HELD  # held", "WATCH  # watchlist"])
        self._queue(tmp_path, "us.txt", ["NEW", "HELD"])
        assert sd.candidates(tmp_path) == ["NEW"]


class TestScreenTicker:
    """One ticker end to end through the fetch seam."""

    def _fetcher(self, monkeypatch, pages):
        calls = []

        def fake(url, timeout=20):
            calls.append(url)
            return pages[url.rsplit("/", 2)[-2]]
        monkeypatch.setattr(sd, "fetch", fake)
        return calls

    def test_one_request_when_the_sector_is_not_needed(self, monkeypatch, tmp_path):
        calls = self._fetcher(monkeypatch, {"statistics": STATS_PAGE})
        s = sd.screen_ticker("BHP.AX", tmp_path)
        assert s is not None
        assert not s.deferred
        assert len(calls) == 1
        assert calls[0].endswith("/statistics/")

    def test_fetches_the_company_page_only_when_debt_would_fire(
            self, monkeypatch, tmp_path):
        geared = STATS_PAGE.replace('value:"0.50"', 'value:"3.50"')
        calls = self._fetcher(monkeypatch, {"statistics": geared,
                                            "company": COMPANY_PAGE})
        s = sd.screen_ticker("BHP.AX", tmp_path)
        assert s.sector == "Materials"
        assert s.reasons == ("D/E 3.5x",)
        assert [c.rsplit("/", 2)[-2] for c in calls] == ["statistics", "company"]

    def test_a_known_sector_saves_the_second_request(self, monkeypatch, tmp_path):
        geared = STATS_PAGE.replace('value:"0.50"', 'value:"3.50"')
        calls = self._fetcher(monkeypatch, {"statistics": geared})
        d = tmp_path / "research" / "BHP.AX"
        d.mkdir(parents=True)
        (d / "info.json").write_text(json.dumps({"sector": "Banks"}))
        s = sd.screen_ticker("BHP.AX", tmp_path)
        assert s.reasons == ()
        assert len(calls) == 1

    def test_a_page_with_no_data_is_none(self, monkeypatch, tmp_path):
        self._fetcher(monkeypatch, {"statistics": "<html></html>"})
        assert sd.screen_ticker("BHP.AX", tmp_path) is None


class TestAge:
    def test_only_stale_entries_are_redone(self):
        state = {"done": {"OLD": "2020-01-01", "NEW": "2099-01-01"},
                 "failed": {}}
        assert sd.pending_by_age(["OLD", "NEW", "X"], state, max_age=180) == \
            ["OLD", "X"]

    def test_no_max_age_means_done_is_done(self):
        state = {"done": {"OLD": "2020-01-01"}, "failed": {}}
        assert sd.pending_by_age(["OLD", "X"], state, max_age=None) == ["X"]


class TestPacingIsShared:
    def test_uses_the_profile_backfills_pacing(self):
        import backfill_profiles as bp
        assert sd.DELAY_SECONDS is bp.DELAY_SECONDS
        assert sd.backoff_seconds is bp.backoff_seconds
        assert sd.EXIT_RATE_LIMITED == bp.EXIT_RATE_LIMITED


class TestCandidateOrder:
    def test_follows_the_selectors_queue_order(self, tmp_path):
        # Screening in the order the selector consumes means the first
        # overnight batch already shapes tomorrow's pick.
        q = tmp_path / "queue"
        q.mkdir()
        (q / "adr.txt").write_text("ADR1\n")
        (q / "nzx.txt").write_text("NZ1\n")
        (q / "zzz_other.txt").write_text("OTHER\n")
        (q / "eu_priority.txt").write_text("EU1\n")
        assert sd.candidates(tmp_path) == ["EU1", "NZ1", "ADR1", "OTHER"]
