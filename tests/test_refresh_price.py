"""refresh_price.py updates a DCF's price-derived numbers without a model.

`weighted_iv` and `entry_price` are mathematically independent of the current
price -- verified against DCBO, where recomputing the weighted IV from the
scenario IVs and weights reproduces the stored 33.0 exactly. Only `upside`
and `entry_discount_from_current` move. So a stale price does not invalidate
a model; it invalidates two derived numbers and the prose around them.

That makes the numeric half free. The prose half is not: 101 of 113 corpus
DCFs quote the price inside sentences whose claims also change (DCBO's base
entry price goes from 15.6% above market to 12.1% below it), so this module
flags those strings and never rewrites them.
"""

import json

import pytest
import refresh_price


def dcf_doc(price=17.52, **extra):
    """A DCF shaped like the corpus files, with DCBO's actual numbers."""
    doc = {
        "ticker": "DCBO",
        "valuation_date": "2026-06-27",
        "current_price": price,
        "currency": "USD",
        "valuation": {
            "bear": {"intrinsic_value": 18.57, "upside": 6.0},
            "base": {"intrinsic_value": 33.33, "upside": 90.2},
            "bull": {"intrinsic_value": 50.11, "upside": 186.0},
        },
        "entry_price": {
            "bear": {"entry_price": 10.81, "entry_discount_from_current": -38.2},
            "base": {"entry_price": 20.26, "entry_discount_from_current": 15.6},
            "bull": {"entry_price": 31.16, "entry_discount_from_current": 78.0},
        },
        "probability_weighted": {
            "weights": {"bear": 0.25, "base": 0.55, "bull": 0.20},
            "weighted_iv": 33.0,
            "weighted_upside": 88.4,
        },
    }
    doc.update(extra)
    return doc


@pytest.fixture
def ticker(tmp_path):
    """A researched ticker; returns a helper to read/write its DCF."""
    reports = tmp_path / "research" / "DCBO" / "Reports"
    reports.mkdir(parents=True)
    path = reports / "DCBO_DCF.json"

    class Fixture:
        repo = tmp_path
        dcf = path

        def write(self, doc):
            path.write_text(json.dumps(doc, indent=2))

        def read(self):
            return json.loads(path.read_text())

    f = Fixture()
    f.write(dcf_doc())
    return f


class TestNumericWriteBack:
    def test_updates_price_and_scenario_upsides(self, ticker):
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        got = ticker.read()
        assert got["current_price"] == 23.06
        # (iv / price - 1) * 100
        assert got["valuation"]["base"]["upside"] == pytest.approx(44.5, abs=0.1)
        assert got["valuation"]["bear"]["upside"] == pytest.approx(-19.5, abs=0.1)
        assert got["valuation"]["bull"]["upside"] == pytest.approx(117.3, abs=0.1)

    def test_updates_entry_discount(self, ticker):
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        entry = ticker.read()["entry_price"]
        # ((price - entry) / price) * -100; base flips above -> below market.
        assert entry["base"]["entry_discount_from_current"] == pytest.approx(
            -12.1, abs=0.1)

    def test_updates_weighted_upside(self, ticker):
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        pw = ticker.read()["probability_weighted"]
        assert pw["weighted_upside"] == pytest.approx(43.1, abs=0.1)

    def test_leaves_weighted_iv_and_entry_price_untouched(self, ticker):
        """The invariant the whole free tier rests on."""
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        got = ticker.read()
        assert got["probability_weighted"]["weighted_iv"] == 33.0
        assert got["entry_price"]["base"]["entry_price"] == 20.26
        for s, iv in (("bear", 18.57), ("base", 33.33), ("bull", 50.11)):
            assert got["valuation"][s]["intrinsic_value"] == iv

    def test_does_not_touch_valuation_date(self, ticker):
        """valuation_date is the staleness key; a price tick is not a valuation."""
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        assert ticker.read()["valuation_date"] == "2026-06-27"

    def test_missing_upside_field_is_not_created(self, ticker):
        # 22 of 113 corpus files have no valuation.{s}.upside at all.
        doc = dcf_doc()
        del doc["valuation"]["base"]["upside"]
        ticker.write(doc)
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        assert "upside" not in ticker.read()["valuation"]["base"]

    def test_investment_thesis_fields_updated_when_present(self, ticker):
        ticker.write(dcf_doc(investment_thesis={
            "current_price": 17.52, "upside_base": "90%"}))
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        thesis = ticker.read()["investment_thesis"]
        assert thesis["current_price"] == 23.06
        assert thesis["upside_base"] == "45%"


class TestProse:
    def test_never_mutates_prose_fields(self, ticker):
        ticker.write(dcf_doc(key_insight="At $17.52, Docebo trades at 1.7x."))
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        assert ticker.read()["key_insight"] == "At $17.52, Docebo trades at 1.7x."

    def test_flags_prose_paths_quoting_previous_price(self, ticker):
        ticker.write(dcf_doc(
            investment_thesis={"key_insight": "At $17.52, cheap."},
            valuation_philosophy="Unrelated prose."))
        result = refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD",
                                       apply=True)
        block = ticker.read()["price_refresh"]
        assert block["prose_is_stale"] is True
        assert "investment_thesis.key_insight" in block[
            "prose_paths_quoting_previous_price"]
        assert "valuation_philosophy" not in block[
            "prose_paths_quoting_previous_price"]
        assert result.prose_paths

    def test_finds_price_inside_lists(self, ticker):
        ticker.write(dcf_doc(sanity_check={"trip_reasons": ["gap to $17.52"]}))
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        flagged = ticker.read()["price_refresh"][
            "prose_paths_quoting_previous_price"]
        assert "sanity_check.trip_reasons[0]" in flagged

    def test_prose_is_stale_false_when_no_prose_quotes_price(self, ticker):
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        assert ticker.read()["price_refresh"]["prose_is_stale"] is False

    def test_comma_grouped_spelling_is_found(self, ticker):
        ticker.write(dcf_doc(price=1234.5, note="trades at $1,234.50 today"))
        refresh_price.refresh(ticker.repo, "DCBO", 1500.0, "USD", apply=True)
        assert "note" in ticker.read()["price_refresh"][
            "prose_paths_quoting_previous_price"]


class TestGuards:
    def test_check_mode_writes_nothing(self, ticker):
        before = ticker.dcf.read_text()
        result = refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD")
        assert ticker.dcf.read_text() == before
        assert result.would_change

    def test_zero_or_negative_price_rejected(self, ticker):
        before = ticker.dcf.read_text()
        for bad in (0.0, -5.0):
            result = refresh_price.refresh(ticker.repo, "DCBO", bad, "USD",
                                           apply=True)
            assert not result.ok
        assert ticker.dcf.read_text() == before

    def test_currency_mismatch_aborts(self, ticker):
        """WISE.L quotes GBp against USD filings; 885.6/48.43 is a coincidence."""
        before = ticker.dcf.read_text()
        result = refresh_price.refresh(ticker.repo, "DCBO", 23.06, "GBp",
                                       apply=True)
        assert not result.ok
        assert "currency" in result.reason
        assert ticker.dcf.read_text() == before

    def test_idempotent_second_run_is_noop(self, ticker):
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        once = ticker.dcf.read_text()
        result = refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD",
                                       apply=True)
        assert not result.would_change
        assert ticker.dcf.read_text() == once

    def test_missing_dcf_is_not_an_error(self, tmp_path):
        (tmp_path / "research").mkdir()
        result = refresh_price.refresh(tmp_path, "NOPE", 10.0, "USD",
                                       apply=True)
        assert not result.ok

    def test_previous_price_recorded(self, ticker):
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        block = ticker.read()["price_refresh"]
        assert block["previous_price"] == 17.52
        assert block["current_price"] == 23.06
        assert block["drift_pct"] == pytest.approx(31.6, abs=0.1)
        assert block["valuation_date"] == "2026-06-27"


class TestQuoteFailuresNeverWrite:
    """A failed fetch must never be mistaken for a real price."""

    @pytest.mark.parametrize("reason", [
        "no such symbol (404)", "fetch failed: HTTP 429",
        "fetch failed: <urlopen error>", "no price",
    ])
    def test_quote_failure_does_not_write(self, ticker, monkeypatch, reason):
        monkeypatch.setattr(refresh_price, "quote",
                            lambda t: (None, reason))
        before = ticker.dcf.read_text()
        result = refresh_price.refresh_ticker(ticker.repo, "DCBO", apply=True)
        assert not result.ok
        assert ticker.dcf.read_text() == before

    def test_live_quote_is_used_when_it_succeeds(self, ticker, monkeypatch):
        monkeypatch.setattr(refresh_price, "quote", lambda t: (23.06, "USD"))
        result = refresh_price.refresh_ticker(ticker.repo, "DCBO", apply=True)
        assert result.ok
        assert ticker.read()["current_price"] == 23.06


class TestDashboardEmbed:
    """Only re-embed where the precondition is verifiable."""

    def dash(self, ticker, payload, price_key="current_price"):
        path = ticker.repo / "research" / "DCBO" / "Reports" / "DCBO_Dashboard.html"
        path.write_text(
            "<html><script>\nconst dcfData = " + payload + ";\n"
            "function render(){}\n</script></html>")
        return path

    def test_dashboard_reembedded_when_price_matches(self, ticker):
        path = self.dash(ticker, json.dumps(dcf_doc()))
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        assert "23.06" in path.read_text()
        assert ticker.read()["price_refresh"]["dashboard_stale"] is False

    def test_dashboard_untouched_when_embedded_price_differs(self, ticker):
        # 62 embedded copies have already drifted from the JSON on disk.
        path = self.dash(ticker, json.dumps(dcf_doc(price=99.99)))
        before = path.read_text()
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        assert path.read_text() == before
        assert ticker.read()["price_refresh"]["dashboard_stale"] is True

    def test_js_literal_dashboard_untouched(self, ticker):
        # 14 dashboards embed JS object literals with unquoted keys.
        path = self.dash(ticker, "{current_price: 17.52, ticker: 'DCBO'}")
        before = path.read_text()
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        assert path.read_text() == before
        assert ticker.read()["price_refresh"]["dashboard_stale"] is True

    def test_missing_dashboard_is_not_an_error(self, ticker):
        result = refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD",
                                       apply=True)
        assert result.ok

    def test_check_mode_leaves_dashboard_alone(self, ticker):
        path = self.dash(ticker, json.dumps(dcf_doc()))
        before = path.read_text()
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD")
        assert path.read_text() == before


class TestMoreGuards:
    def test_dcf_without_current_price_is_refused(self, ticker):
        doc = dcf_doc()
        del doc["current_price"]
        ticker.write(doc)
        result = refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD",
                                       apply=True)
        assert not result.ok
        assert "current_price" in result.reason

    def test_unparseable_dcf_is_refused(self, ticker):
        ticker.dcf.write_text("{not json")
        result = refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD",
                                       apply=True)
        assert not result.ok

    def test_missing_currency_on_either_side_is_allowed(self, ticker):
        doc = dcf_doc()
        del doc["currency"]
        ticker.write(doc)
        assert refresh_price.refresh(ticker.repo, "DCBO", 23.06, "?",
                                     apply=True).ok

    def test_gbx_and_gbp_are_not_interchangeable(self, ticker):
        ticker.write(dcf_doc(currency="GBP"))
        result = refresh_price.refresh(ticker.repo, "DCBO", 100.0, "GBX",
                                       apply=True)
        assert not result.ok

    def test_non_numeric_intrinsic_value_is_skipped(self, ticker):
        doc = dcf_doc()
        doc["valuation"]["base"]["intrinsic_value"] = "n/a"
        ticker.write(doc)
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        assert ticker.read()["valuation"]["base"]["upside"] == 90.2

    def test_non_numeric_entry_price_is_skipped(self, ticker):
        doc = dcf_doc()
        doc["entry_price"]["base"]["entry_price"] = None
        ticker.write(doc)
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        assert ticker.read()["entry_price"]["base"][
            "entry_discount_from_current"] == 15.6


class TestCli:
    def _run(self, monkeypatch, capsys, repo, *argv, price=(23.06, "USD")):
        monkeypatch.setattr(refresh_price, "quote", lambda t: price)
        monkeypatch.setattr(refresh_price, "REPO", repo)
        monkeypatch.setattr("sys.argv", ["refresh_price.py", *argv])
        monkeypatch.setattr(refresh_price.time, "sleep", lambda s: None)
        assert refresh_price.main() == 0
        return capsys.readouterr().out

    def test_check_is_the_default(self, ticker, monkeypatch, capsys):
        before = ticker.dcf.read_text()
        out = self._run(monkeypatch, capsys, ticker.repo, "--ticker", "DCBO")
        assert "nothing written" in out
        assert ticker.dcf.read_text() == before

    def test_apply_writes(self, ticker, monkeypatch, capsys):
        out = self._run(monkeypatch, capsys, ticker.repo,
                        "--ticker", "DCBO", "--apply")
        assert "updated: 1" in out
        assert ticker.read()["current_price"] == 23.06

    def test_check_beats_apply_when_both_given(self, ticker, monkeypatch,
                                               capsys):
        before = ticker.dcf.read_text()
        self._run(monkeypatch, capsys, ticker.repo, "--ticker", "DCBO",
                  "--apply", "--check")
        assert ticker.dcf.read_text() == before

    def test_all_walks_researched_tickers(self, ticker, monkeypatch, capsys):
        out = self._run(monkeypatch, capsys, ticker.repo, "--all", "--apply")
        assert "DCBO" in out

    def test_drift_threshold_skips_small_moves(self, ticker, monkeypatch,
                                               capsys):
        out = self._run(monkeypatch, capsys, ticker.repo, "--all", "--apply",
                        "--drift-pct", "99")
        assert "updated: 0" in out

    def test_quote_failure_is_reported(self, ticker, monkeypatch, capsys):
        out = self._run(monkeypatch, capsys, ticker.repo, "--all",
                        price=(None, "fetch failed: HTTP 429"))
        assert "skipped" in out
        assert "429" in out

    def test_stale_prose_is_surfaced(self, ticker, monkeypatch, capsys):
        ticker.write(dcf_doc(key_insight="At $17.52 it is cheap."))
        out = self._run(monkeypatch, capsys, ticker.repo, "--ticker", "DCBO",
                        "--apply")
        assert "prose now stale" in out

    def test_requires_a_target(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr("sys.argv", ["refresh_price.py"])
        with pytest.raises(SystemExit):
            refresh_price.main()

    def test_no_research_dir(self, tmp_path, monkeypatch, capsys):
        out = self._run(monkeypatch, capsys, tmp_path, "--all")
        assert "would update: 0" in out


class TestFormattingPreserved:
    """A minimal diff is the point: this tool must be reviewable at a glance.

    json.dumps(indent=2) re-flows inline arrays like [22.0, 20.0, 17.0] onto
    one line each, turning a 10-value price update into a 240-line diff that
    buries the real change.
    """

    def test_untouched_inline_arrays_keep_their_shape(self, ticker):
        # Corpus DCFs carry inline arrays that json.dumps would re-flow.
        ticker.dcf.write_text(
            '{\n'
            '  "current_price": 17.52,\n'
            '  "currency": "USD",\n'
            '  "assumptions": {\n'
            '    "fcf_growth_rates": [22.0, 20.0, 17.0]\n'
            '  }\n'
            '}\n')
        before = ticker.dcf.read_text()
        assert "[22.0, 20.0, 17.0]" in before

        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        after = ticker.dcf.read_text()
        assert "[22.0, 20.0, 17.0]" in after
        assert ticker.read()["current_price"] == 23.06

    def test_diff_touches_only_changed_lines(self, ticker):
        before = ticker.dcf.read_text().splitlines()
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        after = ticker.dcf.read_text().splitlines()
        # strict=False: `after` is longer by the appended price_refresh block.
        differing = sum(1 for a, b in zip(before, after, strict=False)
                        if a != b)
        # 10 value lines; everything else is appended price_refresh block.
        assert differing <= 10

    def test_same_named_key_outside_the_parent_is_not_matched(self, ticker):
        """IPL.NZ regression.

        IPL.NZ carries a "base" key at line 92 -- inside an unrelated
        assumptions block -- while the valuation block starts at line 162.
        An unbounded ancestor search latched onto the earlier "base" and
        wrote the bear scenario's upside into it.
        """
        ticker.dcf.write_text(
            '{\n'
            '  "current_price": 17.52,\n'
            '  "currency": "USD",\n'
            '  "assumptions": {\n'
            '    "base": {"note": "decoy", "upside": 999.0}\n'
            '  },\n'
            '  "valuation": {\n'
            '    "base": {"intrinsic_value": 33.33, "upside": 90.2},\n'
            '    "bear": {"intrinsic_value": 18.57, "upside": 6.0}\n'
            '  }\n'
            '}\n')
        refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD", apply=True)
        got = ticker.read()
        assert got["assumptions"]["base"]["upside"] == 999.0   # untouched
        assert got["valuation"]["base"]["upside"] == pytest.approx(44.5, 0.1)
        assert got["valuation"]["bear"]["upside"] == pytest.approx(-19.5, 0.1)


class TestCurrencyAwareIntrinsicValue:
    """`intrinsic_value` is not always denominated like `current_price`.

    23 corpus DCFs carry parallel per-currency IV series, and 15 of those
    leave the top-level `currency` field null, so a currency-code comparison
    cannot catch them. WISE.L is the dangerous shape: the London quote is
    885.6 pence while `intrinsic_value` is 13.5 USD, so recomputing upside
    from the plain field yields -98.5% instead of the correct 13.0% that its
    `intrinsic_value_gbp_pence` series (1001.1) implies.

    The rule: pick the series that matches the quote, and when no series can
    be shown to match, refuse rather than guess.
    """

    def wise_like(self):
        """A dual-denomination file with the plain field in the WRONG unit."""
        return {
            "ticker": "WISE.L", "valuation_date": "2026-08-01",
            "current_price": 885.6, "currency": "USD",
            "valuation": {
                "base": {"intrinsic_value": 13.5,
                         "intrinsic_value_gbp_pence": 1001.1, "upside": 13.0},
                "bear": {"intrinsic_value": 7.68,
                         "intrinsic_value_gbp_pence": 569.4, "upside": -35.7},
                "bull": {"intrinsic_value": 21.03,
                         "intrinsic_value_gbp_pence": 1559.5, "upside": 76.1},
            },
        }

    def test_refuses_when_plain_value_is_a_different_denomination(self, ticker):
        ticker.write(self.wise_like())
        before = ticker.dcf.read_text()
        result = refresh_price.refresh(ticker.repo, "DCBO", 900.0, "GBp",
                                       apply=True)
        assert not result.ok
        assert ticker.dcf.read_text() == before

    def test_never_writes_a_98_percent_loss_from_a_unit_mismatch(self, ticker):
        """The specific corruption this guard exists to prevent."""
        ticker.write(self.wise_like())
        refresh_price.refresh(ticker.repo, "DCBO", 900.0, "GBp", apply=True)
        assert ticker.read()["valuation"]["base"]["upside"] == 13.0

    def test_matching_denomination_still_writes(self, ticker):
        """HFL.NZ shape: plain value agrees with its own _nzd series."""
        ticker.write({
            "ticker": "HFL.NZ", "valuation_date": "2026-08-01",
            "current_price": 6.01, "currency": "NZD",
            "valuation": {"base": {"intrinsic_value": 5.4107,
                                   "intrinsic_value_nzd": 5.4107,
                                   "intrinsic_value_gbp_pence": 235.5354,
                                   "upside": -10.0}},
        })
        result = refresh_price.refresh(ticker.repo, "DCBO", 5.0, "NZD",
                                       apply=True)
        assert result.ok
        assert ticker.read()["valuation"]["base"]["upside"] == pytest.approx(
            8.2, abs=0.1)

    def test_plain_only_file_is_unaffected(self, ticker):
        """The 90 ordinary files must keep working exactly as before."""
        result = refresh_price.refresh(ticker.repo, "DCBO", 23.06, "USD",
                                       apply=True)
        assert result.ok
        assert ticker.read()["valuation"]["base"]["upside"] == pytest.approx(
            44.5, abs=0.1)

    def test_null_currency_still_refuses_a_mismatched_series(self, ticker):
        """The real gap: 15 of the 23 dual files leave `currency` null.

        The currency-code guard cannot fire on those, so the denomination
        check must stand on the numbers alone -- the plain `intrinsic_value`
        sits 98% away from the price while the `_gbp_pence` series sits 13%
        away, which is only explicable as two different units.
        """
        doc = self.wise_like()
        doc["currency"] = None
        ticker.write(doc)
        before = ticker.dcf.read_text()
        result = refresh_price.refresh(ticker.repo, "DCBO", 900.0, None,
                                       apply=True)
        assert not result.ok
        assert "denomination" in result.reason
        assert ticker.dcf.read_text() == before
