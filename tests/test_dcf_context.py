"""scripts/dcf_context.py -- the DCF agent's inputs, printed once.

On TPW.AX the dcf-analyst (Fable, $3.68) spent 12 turns grepping the
same component lines every owner-FCF build needs -- interest income,
lease principal, SBC, buybacks, D&A, diluted shares -- and re-reading the
history it had already been given. This script prints all of it in one
call. Fixtures are trimmed filing excerpts; the DB and Yahoo are stubbed.
"""

import json
from pathlib import Path

import dcf_context
import pytest

FIX = Path(__file__).parent / "fixtures" / "dcf"


@pytest.fixture
def text():
    return (FIX / "TPW_FY2026_components.txt").read_text()


class TestComponents:
    def test_each_component_is_found_with_its_line(self, text):
        hits = dcf_context.grep_components(text)
        by_name: dict[str, dcf_context.Hit] = {}
        for h in hits:
            by_name.setdefault(h.name, h)          # first hit per component
        assert by_name["interest_income"].line.startswith("Interest income")
        assert "Payment of principal portion of lease liabilities" in by_name["lease_principal"].line
        assert "share buy-back" in by_name["buybacks"].line
        assert "Equity-settled share-based payment" in by_name["sbc"].line
        assert by_name["depreciation_amortisation"].line.startswith("Depreciation and amortisation")
        assert by_name["income_tax_paid"].line.startswith("Income tax paid")
        assert by_name["capex_ppe"].line_no > 0

    def test_hits_carry_the_numbers_on_the_line(self, text):
        hits = [h for h in dcf_context.grep_components(text) if h.name == "lease_principal"]
        assert hits[0].values == [-6615.0, -6798.0]        # note ref "5" dropped

    def test_refer_to_note_aside_is_not_a_number(self):
        hits = dcf_context.grep_components(
            "Equity-settled share-based payment expense (refer to note 20)      4,177    4,880\n")
        assert hits[0].values == [4177.0, 4880.0]

    def test_prose_mentions_are_not_hits(self):
        hits = dcf_context.grep_components(
            "The Group's interest income policy is described in note 4.\n")
        assert hits == []


class TestHistoryPivot:
    def test_pivot_sorts_periods_and_marks_fy_basis(self):
        rows = [
            {"period": "H1 FY2026", "revenue": 375.9, "free_cash_flow": 20.0},
            {"period": "FY2025", "revenue": 600.7, "free_cash_flow": 30.1},
            {"period": "FY2024", "revenue": 497.8, "free_cash_flow": 25.0},
        ]
        out = dcf_context.render_history(rows, ["revenue", "free_cash_flow"])
        lines = out.splitlines()
        assert lines[0].split()[:3] == ["period", "revenue", "free_cash_flow"]
        assert [ln.split()[0] for ln in lines[1:]] == ["FY2024", "FY2025", "H1"]  # H1 FY2026 last
        assert "600.7" in lines[2]

    def test_nulls_render_as_dash(self):
        out = dcf_context.render_history([{"period": "FY2024", "revenue": None}], ["revenue"])
        assert out.splitlines()[1].split()[1] == "-"


class TestPrice:
    def test_price_from_yahoo_chart_json(self):
        meta = {"regularMarketPrice": 4.82, "currency": "AUD",
                "regularMarketTime": 1787699400, "marketState": "CLOSED",
                "fiftyTwoWeekHigh": 25.61, "fiftyTwoWeekLow": 4.07}
        p = dcf_context.parse_price(json.dumps({"chart": {"result": [{"meta": meta}]}}))
        assert p.price == 4.82
        assert p.currency == "AUD"
        assert p.as_of.startswith("2026-08-2")
        assert p.low_52w == 4.07

    def test_bad_payload_is_none(self):
        assert dcf_context.parse_price("{}") is None


class TestMemoryLine:
    def test_ticker_memory_line_is_quoted(self, tmp_path):
        (tmp_path / "MEMORY.md").write_text(
            "- [Wise (WISE.L)](wise.md) — Mar-31 FY\n"
            "- [Temple (TPW.AX)](tpw.md) — Jun-30 FY; owner-FCF\n")
        assert dcf_context.memory_line("TPW.AX", tmp_path) == "- [Temple (TPW.AX)](tpw.md) — Jun-30 FY; owner-FCF"
        assert dcf_context.memory_line("XYZ.AX", tmp_path) is None


class TestQuotesDelegation:
    """The Yahoo client is scripts/quotes.py, not a fourth private copy.

    dcf_context printed the price for the DCF agent while refresh_price
    wrote it back, and only one of them applied the info.json price_symbol
    redirect. Sharing the client is what keeps them agreeing.
    """

    def test_fetch_price_goes_through_quotes(self, monkeypatch):
        import quotes

        seen = []

        def fake_live(symbol):
            seen.append(symbol)
            return quotes.Quote(4.82, "AUD", "2026-08-24T00:00:00Z",
                                "CLOSED", 25.61, 4.07)

        monkeypatch.setattr(quotes, "live", fake_live)
        p = dcf_context.fetch_price("TPW.AX")
        assert seen == ["TPW.AX"]
        assert (p.price, p.currency, p.state, p.low_52w) == (4.82, "AUD", "CLOSED", 4.07)

    def test_fetch_failure_stays_none(self, monkeypatch):
        import quotes

        monkeypatch.setattr(quotes, "live", lambda s: None)
        assert dcf_context.fetch_price("NOPE.XX") is None


@pytest.fixture
def apa():
    return (FIX / "APA_FY2026_guidance.txt").read_text()


class TestBalanceAndUnderlyingComponents:
    """What the dcf-analyst grepped the latest annual for on the .AX batch
    (APA.AX, 2026-09-21: 9 filing greps, 72k chars back per run) that the
    component list did not cover: securities on issue, the basic weighted
    average, underlying earnings, net debt and its parts, the tax line,
    and APA's 'security-based' spelling of SBC.
    """

    def test_new_components_are_found(self, apa):
        by: dict[str, dcf_context.Hit] = {}
        for h in dcf_context.grep_components(apa):
            by.setdefault(h.name, h)
        assert by["shares_on_issue"].values[:2] == [1324.0, 1304.0]
        assert by["weighted_avg_shares"].values[:2] == [1316.0, 1295.0]
        assert by["underlying_earnings"].line.startswith("Underlying EBITDA")
        assert by["net_debt"].values[-1] == -12316.0
        assert by["borrowings"].values == [17.0, 1076.0, 4.0] or by["borrowings"].values == [1076.0, 4.0]
        assert by["lease_liabilities"].values[-2:] == [15.0, 13.0]
        assert by["income_tax_expense"].values[:2] == [-166.0, -118.0]
        assert by["sbc"].line.startswith("Cash settled security-based payments")
        assert by["dps"].values[:2] == [58.0, 57.0]

    def test_the_diluted_weighted_average_stays_its_own_component(self):
        hits = dcf_context.grep_components(
            "Weighted average number of ordinary shares (basic)   119,500   118,900\n"
            "Weighted average number of ordinary shares - diluted   121,000   120,100\n")
        assert [h.name for h in hits] == ["weighted_avg_shares", "diluted_shares"]

    def test_underlying_prose_is_not_a_hit(self):
        hits = dcf_context.grep_components(
            "Underlying EBITDA is defined as Earnings before interest, tax, depreciation and amortisation\n"
            "Underlying EBITDA increased 6.2% to $755 million\n")
        # The definition has no number; the segment commentary is prose with
        # a number, which the label anchor cannot tell apart -- one hit.
        assert len(hits) == 1


class TestGuidance:
    """The paragraphs the agent read with sed after grepping for guidance:
    a window around each outlook/guidance mention that also carries a
    fiscal-year token and a figure. Climate-report 'Guidance' does not.
    """

    def test_the_outlook_paragraph_is_returned_as_one_window(self, apa):
        wins = dcf_context.grep_guidance(apa, after_year=2026)
        assert len(wins) == 1
        text = wins[0].text
        assert "guidance of $2,260 million to" in text
        assert "for FY27" in text
        assert "59.0 cents per security" in text
        assert "For personal use only" not in text
        assert "ANNUAL REPORT APA GROUP" not in text
        assert wins[0].line_no > 0

    def test_ghg_protocol_guidance_is_not_a_window(self, apa):
        assert not any("GHG" in w.text for w in dcf_context.grep_guidance(apa, after_year=2026))

    def test_retrospective_guidance_is_not_a_window(self):
        # "in line with guidance" about the year just reported names no
        # later period. On APA.AX FY2026 such mentions filled the window cap
        # before the Outlook section at line 4502 was reached.
        text = ("performance enabled the Board to deliver FY26\n"
                "distributions of 58.0 cents per security, in line with\n"
                "guidance and an increase of 1.8% on FY25.\n")
        assert dcf_context.grep_guidance(text, after_year=2026) == []
        assert len(dcf_context.grep_guidance(text)) == 1        # no year known: keep

    @pytest.mark.parametrize("token", ["FY27", "FY2027", "fiscal 2027", "1H27", "H1 FY27", "2027"])
    def test_future_period_spellings(self, token):
        assert dcf_context.grep_guidance(f"{token} guidance of $5 million\n", after_year=2026)

    def test_climate_targets_are_not_guidance(self):
        # BHP.AX and AD.AS each returned ~11k chars of sustainability prose:
        # "outlook" plus a 2030/2050 target with a percentage. Earnings
        # guidance names the next one to three years.
        text = "Strong growth outlook: we target a 30% reduction in operational emissions by 2030.\n"
        assert dcf_context.grep_guidance(text, after_year=2026) == []
        assert dcf_context.grep_guidance(text.replace("2030", "FY29"), after_year=2026)

    def test_windows_are_capped(self):
        blob = "\n".join(f"FY27 guidance of ${i} million\n" + "filler\n" * 20 for i in range(12))
        assert len(dcf_context.grep_guidance(blob)) == dcf_context.MAX_GUIDANCE_WINDOWS


class TestFilingSelection:
    def test_guidance_reads_the_latest_annual_interim_and_presentation(self, tmp_path):
        for name in ["X_Annual_FY2025.txt", "X_Annual_FY2026.txt", "X_HalfYear_H1-FY2025.txt",
                     "X_HalfYear_H1-FY2026.txt", "X_Presentation_H1-2026.txt", "X_Presentation_FY2024.txt"]:
            (tmp_path / name).write_text("x")
        assert [(p.name, y) for p, y in dcf_context.guidance_files(tmp_path)] == [
            ("X_Annual_FY2026.txt", 2026), ("X_HalfYear_H1-FY2026.txt", 2026),
            ("X_Presentation_H1-2026.txt", 2026)]
