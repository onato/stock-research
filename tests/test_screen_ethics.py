"""Deterministic ethical screening of a ticker's business description.

Stephen does not want to hold companies that deal primarily with animal
products, weapons, surveillance, tobacco, gambling or fossil fuels. The rule
is PRIMARY BUSINESS, not any exposure: a supermarket that sells meat and a
bank that lends to a farm both stay in; a meat processor and a defence prime
do not.

The classifier is keyword-based and deliberately dumb. It reads text the repo
already has (info.json name/sector/quirks, Analysis.json overview and
business_model) and reports which categories it matched and on what evidence.
It never decides on its own to exclude anything: `screen_ethics.py` writes
`ethics` into info.json, and a human promotes a ticker to
state/never_interested.txt. That split matters because the false positives
here are systematic, not random -- "Arms" is a surname (Armstrong), "gun" is
inside "Ginguro", and the word "target" belongs to a retailer as often as a
weapon.

Precision over recall: a missed company can be caught when it is researched
and its Analysis.json is written, but a wrong exclusion silently removes a
good company from a 1,784-name queue where nobody will ever look again.
"""

import json

import pytest
import screen_ethics as se


class TestCategoryMatching:
    """Each category matches its own core vocabulary and nothing else's."""

    @pytest.mark.parametrize(("text", "category"), [
        ("New Zealand's largest integrated seafood company, quota fishing", "animal_products"),
        ("processes and exports beef and lamb from its abattoirs", "animal_products"),
        ("a2 Milk sells infant formula and fresh dairy milk", "animal_products"),
        ("designs and manufactures armoured fighting vehicles and ammunition", "weapons"),
        ("a prime contractor for military aircraft and missile systems", "weapons"),
        ("facial recognition software sold to law enforcement agencies", "surveillance"),
        ("operates private prisons and immigration detention centres", "surveillance"),
        ("manufactures and markets cigarettes and other tobacco products", "tobacco"),
        ("operates casinos and online sports betting platforms", "gambling"),
        ("explores for and produces crude oil and natural gas", "fossil_fuels"),
        ("thermal coal mining for export to power stations", "fossil_fuels"),
    ])
    def test_core_vocabulary_matches(self, text, category):
        got = se.classify(text)
        assert category in got, f"{category!r} not found in {sorted(got)} for {text!r}"

    @pytest.mark.parametrize("text", [
        "operates a chain of supermarkets selling groceries including fresh meat",
        "a retail bank providing mortgages and agricultural lending",
        "designs and sells smartphones, tablets and personal computers",
        "a software company selling language-learning subscriptions",
        "manages commercial office property and industrial warehouses",
    ])
    def test_ordinary_businesses_are_not_flagged(self, text):
        assert se.classify(text) == {}, f"false positive on {text!r}"


class TestFalsePositivesThatActuallyOccur:
    """The substring traps that a naive `in` check walks straight into.

    Every one of these is a real company in the queue or a real English word;
    each was a wrong match before the vocabulary was made word-boundary aware
    and the ambiguous stems were qualified.
    """

    @pytest.mark.parametrize(("text", "why"), [
        ("Armstrong World Industries makes ceiling systems", "'arms' inside 'Armstrong'"),
        ("Ginguro Ltd operates ramen restaurants", "'gun' inside 'Ginguro'"),
        ("Target Corporation is a general merchandise retailer", "'target' is retail here"),
        ("Cochlear designs implantable hearing devices", "'coch' near 'cock'"),
        ("Coalition Housing builds affordable homes", "'coal' inside 'coalition'"),
        ("Gasunie operates regulated gas transmission networks", "infrastructure, not extraction"),
        ("a battery maker supplying the defence of its patents", "'defence' used figuratively"),
        ("Smith & Nephew makes advanced wound-care dressings", "'arms' absent; medical"),
        ("Milkrun is a grocery delivery app", "'milk' inside a brand name"),
        ("Betterware sells household products door to door", "'bet' inside 'Betterware'"),
    ])
    def test_no_match(self, text, why):
        assert se.classify(text) == {}, f"false positive ({why}): {text!r}"


class TestEvidenceIsReported:
    """A flag with no evidence cannot be reviewed, so it is not worth writing."""

    def test_match_reports_the_term_and_the_snippet(self):
        got = se.classify("Sanford is an integrated seafood company built on fishing quota")
        assert "animal_products" in got
        ev = got["animal_products"]
        assert ev["terms"], "no matched terms recorded"
        assert any("fishing" in t or "seafood" in t for t in ev["terms"])
        assert ev["snippet"], "no snippet recorded"

    def test_multiple_categories_are_all_returned(self):
        got = se.classify("operates casinos and also runs a thermal coal mine")
        assert set(got) == {"gambling", "fossil_fuels"}


class TestConfidence:
    """A sector line is a stronger signal than one word in a long summary."""

    def test_sector_match_outranks_a_passing_mention(self):
        strong = se.classify_record({"sector": "Oil & Gas Exploration", "name": "X", "text": ""})
        weak = se.classify_record({
            "sector": "Software", "name": "X",
            "text": "our datacentres are backed up by diesel generators burning fuel oil",
        })
        assert strong["fossil_fuels"]["confidence"] == "high"
        assert weak.get("fossil_fuels", {}).get("confidence") != "high"

    def test_unknown_sector_and_no_text_yields_nothing(self):
        assert se.classify_record({"sector": "Unknown", "name": "Foo Ltd", "text": ""}) == {}


class TestPrimaryBusinessRule:
    """'Deals primarily with' -- incidental exposure is explicitly kept."""

    @pytest.mark.parametrize("text", [
        "a supermarket group; its fresh meat counter is one of many departments",
        "a diversified bank whose agricultural book includes dairy farm lending",
        "an airline; jet fuel is its largest single operating cost",
    ])
    def test_incidental_exposure_is_not_primary(self, text):
        got = se.classify(text)
        for cat, ev in got.items():
            assert ev["confidence"] != "high", (
                f"incidental exposure flagged high for {cat}: {text!r}"
            )


class TestWriteBack:
    """The classifier records into info.json; it never excludes on its own."""

    def test_writes_ethics_block_without_touching_other_fields(self, tmp_path):
        p = tmp_path / "info.json"
        p.write_text(json.dumps({"name": "Sanford Limited", "sector": "Seafood",
                                 "ir_url": "https://example.com"}))
        se.write_info(p, {"animal_products": {"terms": ["seafood"], "snippet": "s",
                                              "confidence": "high"}}, source="test")
        d = json.loads(p.read_text())
        assert d["ir_url"] == "https://example.com", "clobbered an unrelated field"
        assert d["name"] == "Sanford Limited"
        assert d["ethics"]["flags"] == ["animal_products"]
        assert d["ethics"]["source"] == "test"

    def test_no_flags_still_records_that_it_was_checked(self, tmp_path):
        p = tmp_path / "info.json"
        p.write_text(json.dumps({"name": "Duolingo, Inc."}))
        se.write_info(p, {}, source="test")
        d = json.loads(p.read_text())
        assert d["ethics"]["flags"] == []
        assert d["ethics"]["checked_at"], "a clean result must still be dated"

    def test_never_writes_never_interested(self, tmp_path):
        """Exclusion stays a human decision."""
        p = tmp_path / "info.json"
        p.write_text(json.dumps({"name": "BAE Systems plc"}))
        se.write_info(p, {"weapons": {"terms": ["defence"], "snippet": "s",
                                      "confidence": "high"}}, source="test")
        assert not (tmp_path / "never_interested.txt").exists()


class TestSuppliersAndVenuesAreNotTheIndustry:
    """Real false positives from the first full run over the 1,784-name queue.

    Every one of these serves an industry without dealing in its product, which
    is the distinction "deals primarily with" turns on. They are recorded as
    tests because the failure is systematic: the flagged word is genuinely in
    the description, and only the surrounding role makes it innocent.
    """

    NZX = (
        "NZX Limited operates New Zealand's only registered securities exchange, "
        "providing listing, trading, clearing, settlement, depository and data "
        "services across equity, debt, funds, derivatives (dairy and equity) and energy"
    )
    SKL = (
        "Skellerup is a global designer and manufacturer of engineered polymer and "
        "rubber products, operating through two divisions: Agri (dairy rubberware "
        "consumables and Red Band gumboots) and Industrial"
    )
    MOV = (
        "MOVe Logistics Group is one of New Zealand's largest freight and logistics "
        "operators, moving bulk, refrigerated and aquaculture cargo by road and sea"
    )
    HGH = (
        "Heartland Group Holdings is a specialist bank holding company whose lending "
        "book covers motor vehicles, reverse mortgages and livestock finance"
    )
    PYS = (
        "PaySauce is a cloud payroll SaaS and fintech platform for micro and small "
        "employers, with a farm payroll product for dairy operations"
    )

    @pytest.mark.parametrize(("ticker", "why"), [
        ("NZX", "an exchange listing dairy derivatives is not a dairy business"),
        ("SKL", "sells rubberware TO dairy farms; the product is rubber"),
        ("MOV", "hauls the freight, does not farm it"),
        ("HGH", "a bank lending against livestock is not a livestock business"),
        ("PYS", "payroll software sold to dairy farms"),
    ])
    def test_supplier_or_venue_is_not_flagged_high(self, ticker, why):
        got = se.classify(getattr(self, ticker))
        for cat, ev in got.items():
            assert ev["confidence"] != "high", f"{ticker} flagged {cat} high -- {why}"
