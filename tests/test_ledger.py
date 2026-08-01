"""Tests for ledger.py: append-only invariants and idempotency.

All tests run against a patched tmp repo (patch_repo retargets
ledger.LEDGER/LOCK and dcf_fields.REPO, which are bound at import time)
with agents_sha/git_head frozen for determinism.
"""

import json
import shutil
from pathlib import Path

import ledger

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
