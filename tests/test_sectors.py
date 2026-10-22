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

    def test_retirement_services_insurer_is_not_a_village(self):
        # PFG's largest segment is literally named "Retirement and Income
        # Solutions" -- the dcf-analyst's own model prose starts
        # "Retirement-services and insurance FCFE ... at cost of equity",
        # which must route on "at cost of equity" (bank/financial) before
        # the bare word "retirement" is ever checked, or every insurer
        # with a retirement segment mis-routes to the village/ORA template.
        label = (
            "Retirement-services and insurance FCFE (free capital flow) at "
            "cost of equity, net debt held at zero (engine route)"
        )
        assert sectors.bucket("Financials", label) == "bank"


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

    def test_bare_financials_sector_is_bank(self):
        # info.json's `sector` field is often the single word "Financials"
        # (GS, GL, NAB.AX, IAG.AX) with no "financial services"/"bank"/etc
        # qualifier -- that bare string must still route to the bank template.
        assert sectors.bucket("Financials", None) == "bank"

    def test_unknown_is_operating(self):
        assert sectors.bucket("Seafood / Aquaculture", None) == "operating"
        assert sectors.bucket(None, None) == "operating"
        assert sectors.bucket("Unknown", "owner_fcf_dcf_10yr_component_build") == "operating"

    def test_construction_and_mining_machinery_maker_is_not_a_prerevenue_miner(self):
        # 1157.HK (Zoomlion) makes and sells cranes/excavators/concrete
        # machinery -- "mining" here qualifies the equipment market
        # (construction & mining machinery), not an extraction business.
        # The bare "mining" keyword must not fire when the sector string
        # also says "machinery" (the company sells to miners, it isn't one).
        assert sectors.bucket("Industrials - Construction & Mining Machinery", "owner_fcf_dcf_component") == "operating"


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


class TestConsumerInternet:
    """Businesses read as users x monetisation (DAU/MAU, ARPU, subscribers).

    These all fell through to `operating`, so RDDT, META and PINS got no
    user or ARPU chart and the dashboard-generator was never spawned.
    """

    def test_social_and_ad_supported_platforms(self):
        for s in ("Social Media / Online Advertising",          # RDDT
                  "Social Media / Digital Advertising",          # PINS, WB
                  "Social Media / Advertising",                  # META
                  "Technology / Advertising",                    # GOOGL
                  "Internet / Short-Video & Livestreaming",      # 1024.HK
                  "Online Music / Social Entertainment"):        # 1698.HK
            assert sectors.bucket(s, "owner_fcf_dcf") == "consumer_internet", s

    def test_streaming_and_subscription_consumer_apps(self):
        for s in ("Audio Streaming",                                     # SPOT
                  "Communication Services - Entertainment / Streaming Media",  # NFLX
                  "Online Music & Audio Entertainment",                  # TME
                  "Online Video / Gaming / Livestreaming",               # BILI
                  "Online Dating / Consumer Internet"):                  # MTCH
            assert sectors.bucket(s, None) == "consumer_internet", s

    def test_look_alikes_that_are_not_user_businesses(self):
        # An ad agency and ad-tech sell to advertisers, not users; a
        # precious-metals "stream" is a royalty; livestreaming e-commerce
        # is retail; a property portal stays where it was.
        assert sectors.bucket("Advertising & Marketing Services Holding Company",
                              None) == "operating"                       # OMC
        assert sectors.bucket("Advertising Technology / Demand-Side Platform",
                              None) == "operating"                       # TTD
        assert sectors.bucket("Precious Metals Streaming / Royalty", None) == "operating"
        assert sectors.bucket("Consumer Discretionary / Livestreaming E-Commerce",
                              None) == "ecommerce"
        assert sectors.bucket("Real Estate Digital Advertising", None) == "reit"

    def test_it_is_a_known_bucket(self):
        assert "consumer_internet" in sectors.BUCKETS
