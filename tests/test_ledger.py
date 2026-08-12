"""Tests for ledger.py: append-only invariants and idempotency.

All tests run against a patched tmp repo (patch_repo retargets
ledger.LEDGER/LOCK and dcf_fields.REPO, which are bound at import time)
with agents_sha/git_head frozen for determinism.
"""

import json
import runpy
import shutil
import sys
import warnings
from pathlib import Path

import ledger
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def install_dcf(make_ticker, ticker, name=None):
    d = make_ticker(ticker)
    shutil.copy(FIXTURES / "dcf" / f"{name or ticker}_DCF.json",
                d / "Reports" / f"{ticker}_DCF.json")
    return d


class TestRowKey:
    def test_dict_order_independent(self):
        a = {"ticker": "X", "valuation_date": "2026-01-01",
             "weighted_iv": {"weighted_iv": 1.0, "weighted_iv_hkd": 2.0},
             "current_price": 5.0}
        b = dict(a, weighted_iv={"weighted_iv_hkd": 2.0, "weighted_iv": 1.0})
        assert ledger.row_key(a) == ledger.row_key(b)

    def test_price_change_is_a_new_forecast(self):
        a = {"ticker": "X", "valuation_date": "2026-01-01",
             "weighted_iv": {}, "current_price": 5.0}
        b = dict(a, current_price=6.0)
        assert ledger.row_key(a) != ledger.row_key(b)

    def test_logged_at_excluded_from_identity(self):
        a = {"ticker": "X", "valuation_date": "2026-01-01",
             "weighted_iv": {}, "current_price": 5.0, "logged_at": "t1"}
        b = dict(a, logged_at="t2")
        assert ledger.row_key(a) == ledger.row_key(b)


class TestExistingKeys:
    def test_tolerates_garbage_lines(self, patch_repo):
        ledger.LEDGER.write_text(
            json.dumps({"ticker": "X", "valuation_date": "d",
                        "weighted_iv": {}, "current_price": 1.0}) + "\n"
            "not json at all\n"
            "\n")
        keys = ledger.existing_keys()
        assert len(keys) == 1

    def test_missing_file(self, patch_repo):
        assert ledger.existing_keys() == set()


class TestAppend:
    def test_append_then_rerun_is_noop(self, make_ticker, pinned_identity):
        install_dcf(make_ticker, "FRFHF")

        added, skipped, missing = ledger.append(["FRFHF"])
        assert (added, skipped, missing) == (["FRFHF"], [], [])

        added, skipped, missing = ledger.append(["FRFHF"])
        assert (added, skipped, missing) == ([], ["FRFHF"], [])
        assert len(ledger.LEDGER.read_text().splitlines()) == 1

    def test_in_batch_dedup(self, make_ticker, pinned_identity):
        # The same ticker twice in ONE call must also write one row: the
        # in-memory key set is updated as rows are written, not just seeded
        # from the file.
        install_dcf(make_ticker, "FRFHF")
        added, skipped, _missing = ledger.append(["FRFHF", "FRFHF"])
        assert added == ["FRFHF"]
        assert skipped == ["FRFHF"]
        assert len(ledger.LEDGER.read_text().splitlines()) == 1

    def test_missing_dcf_reported_not_written(self, make_ticker, pinned_identity):
        make_ticker("GHOST")
        added, skipped, missing = ledger.append(["GHOST"])
        assert (added, skipped, missing) == ([], [], ["GHOST"])
        assert not ledger.LEDGER.exists() or ledger.LEDGER.read_text() == ""

    def test_rows_store_raw_dicts_deterministically(self, make_ticker, pinned_identity):
        install_dcf(make_ticker, "0285.HK")
        ledger.append(["0285.HK"])
        row = json.loads(ledger.LEDGER.read_text())
        # Raw-dict contract: both currency series stored, no winner picked.
        assert row["weighted_iv"] == {"weighted_iv_hkd": 22.75,
                                      "weighted_iv_rmb": 20.91}
        assert row["agents_sha"] == "testsha00000"
        assert row["git_head"] == "testhead"
        # sort_keys=True: serialization is byte-stable for golden diffs
        assert ledger.LEDGER.read_text() == \
            json.dumps(row, sort_keys=True) + "\n"

    def test_changed_forecast_appends_new_row(self, make_ticker, pinned_identity):
        d = install_dcf(make_ticker, "FRFHF")
        ledger.append(["FRFHF"])

        path = d / "Reports" / "FRFHF_DCF.json"
        doc = json.loads(path.read_text())
        doc["current_price"] = 9999.0
        path.write_text(json.dumps(doc))

        added, _, _ = ledger.append(["FRFHF"])
        assert added == ["FRFHF"]
        assert len(ledger.LEDGER.read_text().splitlines()) == 2


class TestAllTickers:
    def test_sorted_and_stem_must_match_ticker_dir(self, make_ticker, patch_repo):
        """backfill only picks up {TICKER}_DCF.json whose stem matches its
        own ticker directory — a stray foreign DCF file must not seed a row
        under the wrong name."""
        install_dcf(make_ticker, "FRFHF")
        install_dcf(make_ticker, "0285.HK")
        stray = make_ticker("STRAY")
        (stray / "Reports" / "OTHER_DCF.json").write_text("{}")
        assert ledger.all_tickers() == ["0285.HK", "FRFHF"]

    def test_empty_repo(self, patch_repo):
        assert ledger.all_tickers() == []


class TestMain:
    def run(self, monkeypatch, *argv):
        monkeypatch.setattr(sys, "argv", ["ledger.py", *argv])
        return ledger.main()

    def test_no_subcommand_prints_usage(self, monkeypatch, capsys):
        assert self.run(monkeypatch) == 2
        assert "Usage:" in capsys.readouterr().err

    def test_unknown_subcommand_rejected(self, monkeypatch, capsys):
        assert self.run(monkeypatch, "frobnicate") == 2
        assert "Usage:" in capsys.readouterr().err

    def test_append_requires_a_ticker(self, monkeypatch, capsys):
        assert self.run(monkeypatch, "append") == 2
        assert "at least one TICKER" in capsys.readouterr().err

    def test_append_reports_added_and_missing(self, make_ticker,
                                              pinned_identity, monkeypatch,
                                              capsys):
        install_dcf(make_ticker, "FRFHF")
        make_ticker("GHOST")
        assert self.run(monkeypatch, "append", "FRFHF", "GHOST") == 0
        out = capsys.readouterr().out
        assert "1 added, 0 already logged, 1 without DCF.json" in out
        assert "added: FRFHF" in out
        assert "no DCF: GHOST" in out

    def test_backfill_seeds_every_existing_dcf(self, make_ticker,
                                               pinned_identity, monkeypatch,
                                               capsys):
        install_dcf(make_ticker, "FRFHF")
        install_dcf(make_ticker, "0285.HK")
        assert self.run(monkeypatch, "backfill") == 0
        assert "2 added" in capsys.readouterr().out
        assert len(ledger.LEDGER.read_text().splitlines()) == 2

    def test_entrypoint_exits_with_main_status(self, monkeypatch, capsys):
        # No args -> usage -> sys.exit(2), before any file path is touched.
        monkeypatch.setattr(sys, "argv", ["ledger.py"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(SystemExit) as ei:
                runpy.run_module("ledger", run_name="__main__")
        assert ei.value.code == 2


class TestPriceOnlyRefreshNeverAppends:
    """A price tick is an outcome, not a prediction.

    row_key includes current_price (see TestRowKey.test_price_change_is_a_new
    _forecast), so a price-only refresh would otherwise mint a ledger row for
    a forecast nobody made and corrupt the scoring dataset. Suppression is
    structural: refresh_price never calls ledger, and research_one.sh -- the
    only caller of `ledger.py append` -- is not on the tier-0 path.
    """

    def test_refresh_price_does_not_import_ledger(self):
        import refresh_price
        src = Path(refresh_price.__file__).read_text()
        assert "import ledger" not in src
        assert "ledger.append" not in src

    def test_price_only_refresh_appends_no_row(self, make_ticker,
                                               pinned_identity):
        import refresh_price
        d = install_dcf(make_ticker, "FRFHF")
        ledger.append(["FRFHF"])
        before = ledger.LEDGER.read_text()

        dcf = d / "Reports" / "FRFHF_DCF.json"
        doc = json.loads(dcf.read_text())
        price = doc.get("current_price")
        if not isinstance(price, (int, float)) or price <= 0:
            pytest.skip("fixture carries no usable current_price")
        refresh_price.refresh(d.parent.parent, "FRFHF", price * 1.4,
                              doc.get("currency"), apply=True)

        # The refresh must not have appended; only an explicit append can.
        assert ledger.LEDGER.read_text() == before
