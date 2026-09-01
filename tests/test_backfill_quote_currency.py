"""Record each DCF's observed quote currency.

`inputs.quote_currency` became required on 2026-09-01; 146 existing files
predate it. The value must be OBSERVED from a live quote, never inferred from
the exchange suffix -- 3 of 48 bare US symbols (ASML, ADYEY, SPOT) report in
EUR, and an NZX listing says nothing about the filing currency (SMI.NZ and
MKR.NZ file AUD, ARB.NZ files USD).
"""

import backfill_quote_currency as B
import pytest


def test_writes_the_observed_currency(tmp_path):
    d = {"inputs": {"currency": "AUD"}}
    assert B.apply(d, "NZD") is True
    assert d["inputs"]["quote_currency"] == "NZD"


def test_preserves_gbp_pence_case(tmp_path):
    d = {"inputs": {"currency": "USD"}}
    B.apply(d, "GBp")
    assert d["inputs"]["quote_currency"] == "GBp"


def test_is_idempotent():
    d = {"inputs": {"currency": "AUD", "quote_currency": "NZD"}}
    assert B.apply(d, "NZD") is False
    assert d["inputs"]["quote_currency"] == "NZD"


def test_never_overwrites_a_conflicting_existing_value():
    """A stated quote currency that disagrees with the observed one is a
    finding for a human, not something to silently overwrite."""
    d = {"inputs": {"currency": "USD", "quote_currency": "GBP"}}
    with pytest.raises(B.ConflictError):
        B.apply(d, "GBp")


def test_refuses_an_unobserved_currency():
    d = {"inputs": {"currency": "AUD"}}
    for bad in (None, "", "  ", "NZ$", "dollars"):
        with pytest.raises(B.NotObservedError):
            B.apply(d, bad)


def test_adds_an_fx_note_placeholder_only_when_bases_differ():
    d = {"inputs": {"currency": "AUD"}}
    B.apply(d, "NZD")
    assert "fx_note" in d["inputs"]
    same = {"inputs": {"currency": "NZD"}}
    B.apply(same, "NZD")
    assert "fx_note" not in same["inputs"]


def test_does_not_clobber_an_existing_fx_note():
    d = {"inputs": {"currency": "AUD", "fx_note": "AUD/NZD 1.2123, verified"}}
    B.apply(d, "NZD")
    assert d["inputs"]["fx_note"] == "AUD/NZD 1.2123, verified"
