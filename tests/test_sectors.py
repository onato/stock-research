"""Tests for sectors.py: route a ticker to a business-model bucket.

The corpus has 142 free-text sector strings and ~40 DCF model labels;
neither is a taxonomy. The bucket drives which default dashboard template
applies, so the DCF's model label (the valuation agent's own judgment of
what the company is) wins over sector keywords, and anything unrecognised
is an ordinary operating company rather than a guess.
"""

import sectors


class TestModelLabelWins:
    def test_affo_is_a_reit(self):
        assert sectors.bucket("Consumer Discretionary", "AFFO capitalization at cost of equity") == "reit"

    def test_nav_is_a_listed_investment_vehicle(self):
        assert sectors.bucket("Technology", "NAV-per-share compounding with exit price-to-NAV") == "lic_nav"

    def test_book_value_and_earnings_at_coe_are_financials(self):
        assert sectors.bucket("", "Book-value compounding (BVPS growth x exit P/B)") == "bank"
        assert sectors.bucket("", "earnings_based_cost_of_equity") == "bank"
        assert sectors.bucket("", "discounted_distributable_earnings_at_cost_of_equity") == "bank"
        assert sectors.bucket("", "residual_income_on_tangible_book") == "bank"

    def test_risked_project_npv_is_a_prerevenue_miner(self):
        assert sectors.bucket("Gold Mining / Exploration & Development", "risked-project-npv") == "miner_prerevenue"


class TestSectorKeywords:
    def test_saas(self):
        assert sectors.bucket("Software - SaaS / Corporate Learning", "owner-fcf-dcf") == "saas"
        assert sectors.bucket("Application Software", None) == "saas"

    def test_payments(self):
        assert sectors.bucket("Fintech / Payments", None) == "payments"
        assert sectors.bucket("Payment Networks", None) == "payments"

    def test_ecommerce_and_marketplace(self):
        assert sectors.bucket("Consumer Discretionary / Internet & Direct Marketing Retail", None) == "ecommerce"
        assert sectors.bucket("E-Commerce / Cloud (AWS)", None) == "ecommerce"
        assert sectors.bucket("Online Marketplace / Classifieds", None) == "marketplace"

    def test_retirement_villages(self):
        assert sectors.bucket("Healthcare / Retirement Villages & Aged Care", None) == "retirement_ora"

    def test_reit_and_bank_by_sector(self):
        assert sectors.bucket("REIT — Office / Commercial Property", None) == "reit"
        assert sectors.bucket("Banking / Specialist Lending", None) == "bank"
        assert sectors.bucket("Financials / Non-bank Lending (NZ Non-Bank Deposit Taker)", None) == "bank"

    def test_unknown_is_operating(self):
        assert sectors.bucket("Seafood / Aquaculture", None) == "operating"
        assert sectors.bucket(None, None) == "operating"
        assert sectors.bucket("Unknown", "owner_fcf_dcf_10yr_component_build") == "operating"


class TestModelLabel:
    def test_collects_every_spelling_the_corpus_uses(self):
        dcf = {"model_type": "nav_compounding", "inputs": {"model": "x"},
               "valuation_philosophy": {"approach": "Risked project-NPV"}}
        label = sectors.dcf_model_label(dcf)
        assert "nav_compounding" in label
        assert "Risked project-NPV" in label
        assert "Risked project-NPV" not in sectors.dcf_model_label(dcf, include_prose=False)
        assert "nav_compounding" in sectors.dcf_model_label(dcf, include_prose=False)
        assert sectors.dcf_model_label(None) == ""
        assert sectors.dcf_model_label({"valuation_philosophy": "plain string"}) == "plain string"
