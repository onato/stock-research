"""canonical_iv.py resolves a dual-currency DCF's canonical `weighted_iv`.

11 of 121 corpus DCFs carry no `probability_weighted.weighted_iv`. None of
them is missing a valuation -- every one is a dual-listed or dual-currency
name where the agent wrote the value under a currency-suffixed key instead:

    9626.HK   weighted_iv_usd 58.59   weighted_iv_rmb 416.00
    ASML      weighted_iv_eur 639.85  weighted_iv_usd 728.78
    CSU       weighted_intrinsic_value_usd/_cad

screen.py reads exactly `probability_weighted.weighted_iv`, so all 11 land in
`unranked` with NO_IV and drop off the leaderboard silently.

Picking the wrong variant is worse than dropping the row. 9626.HK quotes HKD
138.70; against `weighted_iv_rmb` 416.00 it reads as +200% upside and would
top the leaderboard on an FX artifact. This is the DOW.NZ incident
(+26,884%) and the WISE.L pence/USD trap in CLAUDE.md, one layer up.

So the rule under test is: match the IV to the currency the PRICE is
denominated in, and when that cannot be established, refuse. A NO_IV row is
a visible gap; a wrong row is an investment decision.
"""

import json

import canonical_iv
import pytest


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "research").mkdir()
    return tmp_path


def dcf_at(repo, ticker, probability_weighted, **top):
    base = repo / "research" / ticker / "Reports"
    base.mkdir(parents=True, exist_ok=True)
    payload = {"probability_weighted": probability_weighted, **top}
    path = base / f"{ticker}_DCF.json"
    path.write_text(json.dumps(payload))
    return path


class TestResolve:
    """Selecting the variant that matches the quote currency."""

    def test_picks_the_variant_matching_the_quote_currency(self, repo):
        dcf_at(repo, "9626.HK", {
            "weighted_iv_usd": 58.59,
            "weighted_iv_rmb": 416.0,
            "weighted_iv_hkd": 63.2,
        })
        r = canonical_iv.resolve(repo, "9626.HK", quote_currency="HKD")
        assert r.value == 63.2
        assert r.source_key == "weighted_iv_hkd"

    def test_refuses_when_no_variant_matches_the_quote(self, repo):
        """RMB and USD variants against an HKD quote: no honest answer.

        Converting at a live FX rate would be inventing a number the model
        never produced, so the row stays unranked.
        """
        dcf_at(repo, "9626.HK", {
            "weighted_iv_usd": 58.59,
            "weighted_iv_rmb": 416.0,
        })
        r = canonical_iv.resolve(repo, "9626.HK", quote_currency="HKD")
        assert r.value is None
        assert "no HKD variant" in r.reason

    def test_refuses_when_the_quote_currency_is_unknown(self, repo):
        """CSU: Yahoo has no quote for the bare symbol (it is CSU.TO).

        Without a quote currency there is nothing to match against, and the
        two variants differ by 42%.
        """
        dcf_at(repo, "CSU", {
            "weighted_intrinsic_value_usd": 2068.47,
            "weighted_intrinsic_value_cad": 2936.08,
        })
        r = canonical_iv.resolve(repo, "CSU", quote_currency=None)
        assert r.value is None
        assert "unknown quote currency" in r.reason

    def test_reads_the_alternate_weighted_intrinsic_value_spelling(self, repo):
        dcf_at(repo, "CSU", {
            "weighted_intrinsic_value_usd": 2068.47,
            "weighted_intrinsic_value_cad": 2936.08,
        })
        r = canonical_iv.resolve(repo, "CSU", quote_currency="CAD")
        assert r.value == 2936.08
        assert r.source_key == "weighted_intrinsic_value_cad"

    def test_single_variant_still_must_match_the_quote(self, repo):
        """ARB.NZ: the lone variant is NZD and the quote is NZD -- fine.

        The single-variant case is NOT a free pass. ARB.NZ's `inputs.currency`
        says USD while the IV key says NZD; trusting "there is only one, use
        it" without checking would reintroduce the mismatch this guards.
        """
        dcf_at(repo, "ARB.NZ", {"weighted_iv_nzd": 0.0219})
        assert canonical_iv.resolve(
            repo, "ARB.NZ", quote_currency="NZD").value == 0.0219
        assert canonical_iv.resolve(
            repo, "ARB.NZ", quote_currency="USD").value is None

    def test_existing_canonical_key_wins_untouched(self, repo):
        """A file that already has `weighted_iv` is not a candidate at all."""
        dcf_at(repo, "DCBO", {"weighted_iv": 33.0})
        r = canonical_iv.resolve(repo, "DCBO", quote_currency="USD")
        assert r.value == 33.0
        assert r.already_canonical is True

    def test_zero_is_a_real_verdict_not_a_missing_value(self, repo):
        """CBD.NZ's receivership waterfall leaves nil to equity.

        screen.py already tests `iv is None` rather than truthiness for this
        reason; the resolver must not undo that with a falsy check.
        """
        dcf_at(repo, "CBD.NZ", {"weighted_iv_nzd": 0.0})
        r = canonical_iv.resolve(repo, "CBD.NZ", quote_currency="NZD")
        assert r.value == 0.0
        assert r.already_canonical is False

    def test_gbp_pence_is_not_gbp(self, repo):
        """WISE.L quotes GBp against GBP filings -- a 100x error if mixed."""
        dcf_at(repo, "WISE.L", {"weighted_iv_gbp": 8.85})
        r = canonical_iv.resolve(repo, "WISE.L", quote_currency="GBp")
        assert r.value is None
        assert "no GBp variant" in r.reason

    def test_non_numeric_variant_is_ignored(self, repo):
        dcf_at(repo, "X", {"weighted_iv_usd": "n/a"})
        assert canonical_iv.resolve(repo, "X", quote_currency="USD").value is None

    def test_missing_file_is_not_an_error(self, repo):
        r = canonical_iv.resolve(repo, "NOPE", quote_currency="USD")
        assert r.value is None


class TestApply:
    """Writing the resolved value back into the DCF."""

    def test_apply_writes_the_canonical_key(self, repo):
        path = dcf_at(repo, "9999.HK", {
            "weighted_iv_rmb": 238.81,
            "weighted_iv_hkd": 257.91,
        })
        assert canonical_iv.apply(repo, "9999.HK", quote_currency="HKD") is True
        pw = json.loads(path.read_text())["probability_weighted"]
        assert pw["weighted_iv"] == 257.91
        # The currency-suffixed originals are evidence; they stay.
        assert pw["weighted_iv_rmb"] == 238.81

    def test_apply_records_which_variant_it_took(self, repo):
        path = dcf_at(repo, "9999.HK", {
            "weighted_iv_rmb": 238.81,
            "weighted_iv_hkd": 257.91,
        })
        canonical_iv.apply(repo, "9999.HK", quote_currency="HKD")
        pw = json.loads(path.read_text())["probability_weighted"]
        assert pw["weighted_iv_source"] == "weighted_iv_hkd"

    def test_apply_refuses_on_a_mismatch(self, repo):
        path = dcf_at(repo, "9626.HK", {"weighted_iv_rmb": 416.0})
        assert canonical_iv.apply(repo, "9626.HK", quote_currency="HKD") is False
        assert "weighted_iv" not in json.loads(
            path.read_text())["probability_weighted"]

    def test_apply_never_touches_valuation_date(self, repo):
        """Same rule refresh_price.py documents: this is not a new valuation."""
        path = dcf_at(repo, "9999.HK",
                      {"weighted_iv_hkd": 257.91},
                      valuation_date="2026-06-27")
        canonical_iv.apply(repo, "9999.HK", quote_currency="HKD")
        assert json.loads(path.read_text())["valuation_date"] == "2026-06-27"

    def test_apply_preserves_the_rest_of_the_file_verbatim(self, repo):
        """Only the two new keys may appear in the diff.

        `json.dumps(indent=2)` renormalises the whole document -- it drops the
        blank lines 9 corpus DCFs use to separate blocks and rewrites `2.50`
        as `2.5`. Semantically identical, but it turned this 9-file change
        into 1804 changed lines and buried the actual edit. refresh_price.py
        carries the same rule for the same reason.
        """
        base = repo / "research" / "T" / "Reports"
        base.mkdir(parents=True)
        original = (
            '{\n'
            '  "ticker": "T",\n'
            '\n'
            '  "equity_5yr_cagr": 2.50,\n'
            '  "rates": [22.0, 20.0, 17.0],\n'
            '\n'
            '  "probability_weighted": {\n'
            '    "weighted_iv_hkd": 257.91\n'
            '  }\n'
            '}\n'
        )
        path = base / "T_DCF.json"
        path.write_text(original)

        assert canonical_iv.apply(repo, "T", quote_currency="HKD") is True
        after = path.read_text()

        assert '"equity_5yr_cagr": 2.50' in after      # not renormalised
        assert '"rates": [22.0, 20.0, 17.0]' in after  # not re-flowed
        assert '\n\n  "probability_weighted"' in after  # blank lines kept
        pw = json.loads(after)["probability_weighted"]
        assert pw["weighted_iv"] == 257.91
        assert pw["weighted_iv_source"] == "weighted_iv_hkd"

    def test_apply_falls_back_rather_than_corrupt(self, repo):
        """An unparseable result must never reach disk."""
        base = repo / "research" / "T" / "Reports"
        base.mkdir(parents=True)
        path = base / "T_DCF.json"
        path.write_text(json.dumps(
            {"probability_weighted": {"weighted_iv_hkd": 257.91}}))
        assert canonical_iv.apply(repo, "T", quote_currency="HKD") is True
        json.loads(path.read_text())  # parses

    def test_apply_is_idempotent(self, repo):
        path = dcf_at(repo, "9999.HK", {"weighted_iv_hkd": 257.91})
        assert canonical_iv.apply(repo, "9999.HK", quote_currency="HKD") is True
        first = path.read_text()
        assert canonical_iv.apply(repo, "9999.HK", quote_currency="HKD") is False
        assert path.read_text() == first
