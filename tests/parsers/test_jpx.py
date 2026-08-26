"""JPX (.T) parser tests. Fixture: tests/fixtures/extracted/jpx/.

Japanese tanshin (決算短信) are the statement-bearing filings for a Tokyo
listing, and nothing in the generic English vocabulary reads them. Four
quirks, all of them structural rather than one company's formatting:

  * labels are CJK, so LABEL_RE (anchored on [A-Za-z(]) rejects every line;
  * the minus sign is "△" (and sometimes "▲"), not a bracket or a hyphen;
  * units are declared as "（単位：百万円）" — millions of yen;
  * **column order is reversed**: 前連結会計年度 (prior year) prints FIRST and
    当連結会計年度 (current year) SECOND, the opposite of the Western layout
    BaseParser assumes. Reading column 0 as the reporting period would file
    every figure one year late.
"""

import pytest
from parsers import get_parser


def parser():
    return get_parser("7313.T")


@pytest.fixture
def facts(fixture_text):
    return list(parser().scan(
        fixture_text("jpx", "TSTECH_tanshin_fy2026.txt"),
        "7313.T_Annual_FY2026.txt"))


def vals(facts, metric, period=None):
    """Distinct values for a metric/period.

    A tanshin states its headline figures twice -- once on the summary page
    and again in the statement below -- so the same number legitimately
    arrives as two corroborating candidates. Deduplicate for assertions;
    the facts table deliberately keeps both.
    """
    seen = [f["value_raw"] for f in facts
            if f["metric"] == metric and (period is None or f["period"] == period)]
    return sorted(set(seen))


class TestRouting:
    def test_t_suffix_routes_to_jpx_parser(self):
        assert type(parser()).__name__ == "JPXParser"

    def test_other_suffixes_unaffected(self):
        assert type(get_parser("AGL.NZ")).__name__ == "NZXParser"
        assert type(get_parser("AAPL")).__name__ == "BaseParser"


class TestUnitsAndCurrency:
    def test_millions_of_yen_declaration(self, facts):
        assert facts, "expected facts from the tanshin statements"
        assert {f["units_hint"] for f in facts} == {"millions"}

    def test_currency_is_jpy(self, facts):
        assert {f["currency"] for f in facts} == {"JPY"}


class TestNumberGrammar:
    def test_triangle_is_a_minus_sign(self):
        # 売上原価 △387,874 is a cost of 387,874, not a positive number.
        text = ("（単位：百万円）\n"
                "                前連結会計年度   当連結会計年度\n"
                "売上原価            △397,547        △387,874\n")
        facts = list(parser().scan(text, "7313.T_Annual_FY2026.txt"))
        got = {(f["period"], f["value_raw"]) for f in facts
               if f["metric"] == "CostOfRevenue"}
        assert got == {("FY2026", -387874.0), ("FY2025", -397547.0)}

    def test_black_triangle_also_negative(self):
        text = ("（単位：百万円）\n"
                "                前連結会計年度   当連結会計年度\n"
                "営業利益            ▲1,000          ▲2,000\n")
        facts = list(parser().scan(text, "7313.T_Annual_FY2026.txt"))
        assert vals(facts, "OperatingIncome", "FY2026") == [-2000.0]


class TestReversedColumnOrder:
    """当連結会計年度 (current) is the SECOND column, not the first."""

    def test_revenue_current_year_is_second_column(self, facts):
        # 売上収益  460,514 (FY2025)  442,316 (FY2026)
        assert vals(facts, "Revenue", "FY2026") == [442316.0]
        assert vals(facts, "Revenue", "FY2025") == [460514.0]

    def test_operating_income(self, facts):
        assert vals(facts, "OperatingIncome", "FY2026") == [10325.0]
        assert vals(facts, "OperatingIncome", "FY2025") == [16428.0]

    def test_current_column_is_the_statement_line_confidence(self, facts):
        conf = {f["confidence"] for f in facts if f["metric"] == "Revenue"
                and f["period"] == "FY2026"}
        assert "statement_line" in conf
        prior = {f["confidence"] for f in facts if f["metric"] == "Revenue"
                 and f["period"] == "FY2025"}
        assert prior == {"prior_year_column"}


class TestVocabulary:
    def test_gross_profit(self, facts):
        assert vals(facts, "GrossProfit", "FY2026") == [54441.0]

    def test_profit_before_tax(self, facts):
        assert 15461.0 in vals(facts, "ProfitBeforeTax", "FY2026")

    def test_net_income_is_attributable_to_owners_not_group(self, facts):
        # 当期利益 9,658 is the GROUP figure; 親会社の所有者に帰属する当期利益
        # 7,134 is what an equity holder owns. NCI is material here
        # (2,524m, 26% of group profit), so both must be candidates and the
        # attributable one must be present for the agent to choose.
        ni = vals(facts, "NetIncome", "FY2026")
        assert 7134.0 in ni, "attributable-to-owners profit missing"

    def test_nci_captured_separately(self, facts):
        assert 2524.0 in vals(facts, "MinorityInterest", "FY2026")

    def test_total_assets(self, facts):
        assert vals(facts, "TotalAssets", "FY2026") == [422709.0]
        assert vals(facts, "TotalAssets", "FY2025") == [432366.0]

    def test_total_liabilities(self, facts):
        assert 95111.0 in vals(facts, "TotalLiabilities", "FY2026")

    def test_equity_attributable_to_owners(self, facts):
        # 親会社の所有者に帰属する持分合計 309,869 -- not 資本合計 327,598,
        # which includes the 17,728 of non-controlling interests.
        assert 309869.0 in vals(facts, "ShareholdersEquity", "FY2026")

    def test_cash_and_equivalents(self, facts):
        assert 92602.0 in vals(facts, "CashAndEquivalents", "FY2026")

    def test_operating_cash_flow(self, facts):
        assert 22607.0 in vals(facts, "OperatingCashFlow", "FY2026")

    def test_capex_is_ppe_purchases(self, facts):
        # 有形固定資産の取得による支出 △19,246
        assert -19246.0 in vals(facts, "CapEx", "FY2026")

    def test_depreciation(self, facts):
        assert 14447.0 in vals(facts, "Depreciation", "FY2026")

    def test_eps_from_statement(self):
        # 基本的１株当たり当期利益（円）70.69 (prior) / 60.37 (current).
        text = ("2026年３月期 決算短信〔ＩＦＲＳ〕(連結)\n"
                "（単位：百万円）\n"
                "                          前連結会計年度   当連結会計年度\n"
                " 基本的１株当たり当期利益（円）        70.69            60.37\n")
        facts = list(parser().scan(text, "7313.T_Annual_FY2026.txt"))
        assert vals(facts, "EPS", "FY2026") == [60.37]
        assert vals(facts, "EPS", "FY2025") == [70.69]

    def test_dividends_paid(self, facts):
        assert -10352.0 in vals(facts, "DividendsPaid", "FY2026")

    def test_share_repurchases(self, facts):
        assert -4999.0 in vals(facts, "ShareRepurchases", "FY2026")

    def test_lease_principal_repayments(self, facts):
        # リース負債の返済による支出 -- the IFRS-16 financing outflow that
        # must be deducted for owner FCF.
        assert -1503.0 in vals(facts, "LeasePrincipalPaid", "FY2026")


class TestSummaryPageRowLayout:
    """The 決算短信 front page tabulates one fiscal year PER ROW, labelled
    '2026年３月期'. That label is authoritative and overrides the reversed
    column rule, which applies only to the two-column statements."""

    def test_summary_row_period_from_its_own_label(self, facts):
        # (2) 連結財政状態: 2026年３月期 422,709 / 2025年３月期 432,366
        assert 422709.0 in vals(facts, "TotalAssets", "FY2026")
        assert 432366.0 in vals(facts, "TotalAssets", "FY2025")

    def test_summary_cash_flow_row(self, facts):
        assert 22607.0 in vals(facts, "OperatingCashFlow", "FY2026")
        assert 28713.0 in vals(facts, "OperatingCashFlow", "FY2025")


class TestPeriodLabelling:
    def test_interim_filing_is_h1_of_the_ending_fiscal_year(self):
        # 2026年３月期 第２四半期(中間期) covers Apr-Sep 2025 and belongs to
        # the FY ending 2026-03-31 -> H1 FY2026.
        text = ("2026年３月期 第２四半期（中間期）決算短信〔ＩＦＲＳ〕（連結）\n"
                "（単位：百万円）\n"
                "                前連結会計年度   当連結会計年度\n"
                "売上収益            230,000        220,000\n")
        facts = list(parser().scan(text, "7313.T_HalfYear_H1-2026.txt"))
        assert vals(facts, "Revenue", "H1 FY2026") == [220000.0]
        assert vals(facts, "Revenue", "H1 FY2025") == [230000.0]

    def test_q1_filing(self):
        text = ("2027年３月期 第１四半期決算短信〔ＩＦＲＳ〕（連結）\n"
                "（単位：百万円）\n"
                "                前第１四半期     当第１四半期\n"
                "売上収益            110,000        105,000\n")
        facts = list(parser().scan(text, "7313.T_Quarterly_Q1-2027.txt"))
        assert vals(facts, "Revenue", "Q1 FY2027") == [105000.0]
        assert vals(facts, "Revenue", "Q1 FY2026") == [110000.0]
