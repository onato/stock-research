"""Deterministic pre-adjudication of `facts` into a worksheet.

The financial-parser agent spent ~65% of its turns grepping filings it had
already had scanned into `facts` (ARB.NZ: 81 of 124 tool calls). These tests
pin the decision ladder that hands it a worksheet instead: resolve the cells
the candidates already settle, rank the rest, and name what is missing.

Synthetic facts only -- tests never read research/.
"""

import sys

import adjudicate
import pytest
import sections

FACT_COLS = ("metric", "period", "value_raw", "units_hint", "source_file",
             "line_no", "context", "confidence", "currency")


def fact(metric, period, value, *, file="X.NZ_Annual_FY2024.txt", line=100,
         context="Consolidated statement of comprehensive income",
         confidence="statement_line", units="thousands", currency="NZD"):
    return (metric, period, value, units, file, line, context, confidence, currency)


@pytest.fixture
def db(mem_db):
    def _load(rows):
        mem_db.executemany(
            f"INSERT INTO facts ({', '.join(FACT_COLS)}) VALUES "
            f"({', '.join('?' * len(FACT_COLS))})", rows)
        return mem_db
    return _load


def cell(proposals, metric, period):
    return next(p for p in proposals if p.metric == metric and p.period == period)


class TestLadder:
    def test_single_candidate_resolves(self, db):
        con = db([fact("Revenue", "FY2024", 263527.0)])
        p = cell(adjudicate.propose(con), "revenue", "FY2024")
        assert (p.status, p.rung, p.value_raw) == ("resolved", "single", 263527.0)
        assert p.units_hint == "thousands"
        assert p.currency == "NZD"
        assert p.source_file == "X.NZ_Annual_FY2024.txt"
        assert p.line_no == 100

    def test_unanimous_candidates_resolve(self, db):
        con = db([fact("Revenue", "FY2024", 263527.0, line=100),
                  fact("Revenue", "FY2024", 263527.0, line=900,
                       context="Note 3 segment revenue")])
        p = cell(adjudicate.propose(con), "revenue", "FY2024")
        assert (p.status, p.rung) == ("resolved", "unanimous")
        assert p.n_candidates == 2

    def test_later_filings_comparative_corroborates(self, db):
        # FY2024's own filing offers two values; FY2025's comparative column
        # repeats one of them. That is the tie-breaker.
        con = db([fact("Revenue", "FY2024", 263527.0, line=100),
                  fact("Revenue", "FY2024", 12000.0, line=880,
                       context="Note 4 Revenue from contracts: services"),
                  fact("Revenue", "FY2024", 263527.0, file="X.NZ_Annual_FY2025.txt",
                       confidence="prior_year_column")])
        p = cell(adjudicate.propose(con), "revenue", "FY2024")
        assert (p.status, p.rung, p.value_raw) == ("resolved", "corroborated", 263527.0)
        assert "X.NZ_Annual_FY2025.txt" in p.rationale

    def test_disagreement_without_corroboration_is_contested(self, db):
        con = db([fact("Revenue", "FY2024", 12000.0, line=880,
                       context="Note 4 Revenue from contracts: services"),
                  fact("Revenue", "FY2024", 263527.0, line=100)])
        p = cell(adjudicate.propose(con), "revenue", "FY2024")
        assert p.status == "contested"
        assert p.value_raw is None
        # statement caption outranks a note, regardless of insertion order
        assert [c.value_raw for c in p.shortlist] == [263527.0, 12000.0]
        assert p.shortlist[0].line_no == 100

    def test_shortlist_is_capped(self, db):
        con = db([fact("Revenue", "FY2024", float(v), line=v) for v in range(1, 9)])
        p = cell(adjudicate.propose(con), "revenue", "FY2024")
        assert p.status == "contested"
        assert len(p.shortlist) == adjudicate.SHORTLIST

    def test_prose_alone_is_contested_not_resolved(self, db):
        con = db([fact("Revenue", "FY2024", 263.5, confidence="prose",
                       context="revenue grew to $263.5m")])
        p = cell(adjudicate.propose(con), "revenue", "FY2024")
        assert p.status == "contested"
        assert p.shortlist[0].confidence == "prose"

    def test_missing_cell_names_that_periods_files(self, db):
        con = db([fact("Revenue", "FY2024", 1.0),
                  fact("Revenue", "FY2023", 1.0, file="X.NZ_Annual_FY2023.txt"),
                  fact("NetIncome", "FY2023", 1.0, file="X.NZ_Annual_FY2023.txt")])
        p = cell(adjudicate.propose(con), "net_income", "FY2024")
        assert p.status == "missing"
        assert p.files == ["X.NZ_Annual_FY2024.txt"]

    def test_year_header_captured_as_value_cannot_resolve(self, db):
        # "EBITDA   2024   2023" -- the scanner read the column headers as
        # values, and every copy agrees, so unanimity alone would bless it.
        con = db([fact("EBITDA", "FY2024", 2024.0, line=50),
                  fact("EBITDA", "FY2024", 2024.0, line=90)])
        p = cell(adjudicate.propose(con), "ebitda", "FY2024")
        assert p.status == "contested"
        assert "year" in p.rationale

    def test_year_like_value_with_other_evidence_is_ignored(self, db):
        con = db([fact("Revenue", "FY2024", 2024.0, line=50),
                  fact("Revenue", "FY2024", 263527.0, line=100)])
        p = cell(adjudicate.propose(con), "revenue", "FY2024")
        assert (p.status, p.rung, p.value_raw) == ("resolved", "single", 263527.0)

    def test_only_comparative_evidence_is_contested(self, db):
        # No filing of FY2023's own, but FY2024's comparative column has it.
        # Worth showing the agent; not strong enough to resolve alone.
        con = db([fact("Revenue", "FY2024", 2.0),
                  fact("Revenue", "FY2023", 1.0, confidence="prior_year_column")])
        p = cell(adjudicate.propose(con), "revenue", "FY2023")
        assert p.status == "contested"
        assert p.shortlist[0].confidence == "prior_year_column"


class TestSectionAndStaticGuards:
    def secs(self):
        import sections
        return {"X.NZ_Annual_FY2024.txt": [
            sections.Section("summary", "FINANCIAL SUMMARY", 1, 50),
            sections.Section("statement", "STATEMENT OF FINANCIAL POSITION", 51, 120),
            sections.Section("notes", "NOTES", 121, 999)]}

    def test_summary_table_value_cannot_resolve_alone(self, db):
        # A lone candidate inside the five-year summary is usually another
        # year's figure (ARG.NZ FY2020 equity 810.4 was FY2016's).
        con = db([fact("ShareholdersEquity", "FY2024", 810.4, line=20)])
        p = cell(adjudicate.propose(con, self.secs()), "shareholders_equity", "FY2024")
        assert p.status == "contested"
        assert "summary" in p.rationale
        assert p.shortlist[0].section == "summary"

    def test_statement_section_outranks_summary_and_notes(self, db):
        con = db([fact("Revenue", "FY2024", 1.0, line=20),
                  fact("Revenue", "FY2024", 3.0, line=300),
                  fact("Revenue", "FY2024", 2.0, line=60)])
        p = cell(adjudicate.propose(con, self.secs()), "revenue", "FY2024")
        assert [c.value_raw for c in p.shortlist] == [2.0, 3.0, 1.0]

    def test_within_a_statement_section_the_earlier_line_wins(self, db):
        # 0001.HK cash: the cash-flow statement's opening balance (context
        # says "cash flow") must not outrank the balance-sheet line.
        secs = {"X.NZ_Annual_FY2024.txt": [
            sections.Section("statement", "FINANCIAL POSITION", 51, 120),
            sections.Section("statement", "CASH FLOWS", 121, 200)]}
        con = db([fact("CashAndEquivalents", "FY2024", 127323.0, line=150,
                       context="Consolidated statement of cash flows"),
                  fact("CashAndEquivalents", "FY2024", 121303.0, line=60,
                       context="Current assets")])
        p = cell(adjudicate.propose(con, secs), "cash_and_equivalents", "FY2024")
        assert [c.value_raw for c in p.shortlist] == [121303.0, 127323.0]

    def test_home_statement_outranks_another_statement(self, db):
        # 0004.HK cash: the cash-flow statement's closing balance and the
        # balance sheet's "Bank deposits and cash" are both statement lines;
        # a balance-sheet metric belongs to the balance sheet.
        secs = {"X.NZ_Annual_FY2024.txt": [
            sections.Section("statement", "Consolidated Statement of Cash Flows", 1, 100),
            sections.Section("statement", "Consolidated Statement of Financial Position", 101, 200)]}
        con = db([fact("CashAndEquivalents", "FY2024", 8964.0, line=90),
                  fact("CashAndEquivalents", "FY2024", 9718.0, line=150)])
        p = cell(adjudicate.propose(con, secs), "cash_and_equivalents", "FY2024")
        assert [c.value_raw for c in p.shortlist] == [9718.0, 8964.0]

    def test_shareholders_equity_is_definition_sensitive(self, db):
        con = db([fact("ShareholdersEquity", "FY2024", 152418.0)])
        assert cell(adjudicate.propose(con), "shareholders_equity", "FY2024").flag == "confirm-definition"

    def test_value_repeating_across_periods_is_static_text(self, db):
        # ARG.NZ: capex "33,220" in five different periods -- a facility limit
        # or commitment line that matched the pattern, not a cash flow.
        rows = [fact("CapEx", f"FY{y}", 33220.0, line=500, file=f"X.NZ_Annual_FY{y}.txt")
                for y in (2022, 2023, 2024)]
        rows.append(fact("CapEx", "FY2024", -812.0, line=200))
        p = cell(adjudicate.propose(con := db(rows)), "capex", "FY2024")
        assert (p.status, p.rung, p.value_raw) == ("resolved", "single", -812.0)
        q = cell(adjudicate.propose(con), "capex", "FY2023")
        assert q.status == "contested"
        assert "repeat" in q.rationale

    def test_share_count_from_a_note_cannot_resolve_alone(self, db):
        # 0001.HK: "8,000,000,000 shares" is the AUTHORISED capital in the
        # share-capital note; it repeated every year so the static guard's
        # share-count exemption let it resolve as shares outstanding.
        secs = {"X.NZ_Annual_FY2024.txt": [
            sections.Section("statement", "FINANCIAL POSITION", 1, 100),
            sections.Section("notes", "NOTES", 101, 999)]}
        con = db([fact("SharesOutstanding", "FY2024", 8e9, line=500)])
        p = cell(adjudicate.propose(con, secs), "shares_outstanding", "FY2024")
        assert p.status == "contested"
        assert "note" in p.rationale
        con2 = db([fact("SharesOutstanding", "FY2024", 3830.0, line=50)])
        assert cell(adjudicate.propose(con2, secs), "shares_outstanding", "FY2024").status == "resolved"

    def test_negative_count_or_total_is_a_stray_match(self, db):
        # 0001.HK: shares_outstanding = -41 resolved as the lone candidate.
        con = db([fact("SharesOutstanding", "FY2024", -41.0, line=10)])
        p = cell(adjudicate.propose(con), "shares_outstanding", "FY2024")
        assert p.status == "contested"
        assert "negative" in p.rationale

    def test_zero_eps_is_a_dash_row(self, db):
        con = db([fact("EPS", "FY2024", 0.0, line=10), fact("EPS", "FY2024", 4.46, line=60)])
        assert cell(adjudicate.propose(con), "eps", "FY2024").value_raw == 4.46

    def test_zero_net_income_is_a_dash_row(self, db):
        con = db([fact("NetIncome", "FY2024", 0.0, line=10),
                  fact("NetIncome", "FY2024", 17088.0, line=60)])
        assert cell(adjudicate.propose(con), "net_income", "FY2024").value_raw == 17088.0

    def test_zero_total_is_a_dash_row(self, db):
        con = db([fact("TotalAssets", "FY2024", 0.0, line=10),
                  fact("TotalAssets", "FY2024", 206.3, line=60)])
        p = cell(adjudicate.propose(con), "total_assets", "FY2024")
        assert (p.status, p.value_raw) == ("resolved", 206.3)
        con2 = db([fact("TotalAssets", "FY2023", 0.0, line=10, file="X.NZ_Annual_FY2023.txt")])
        assert cell(adjudicate.propose(con2), "total_assets", "FY2023").status == "contested"

    def test_lone_outlier_against_other_periods_is_contested(self, db):
        # SEK.NZ H1 FY2020 shares_outstanding: a single match of 358 when every
        # other period reads ~40,000. One candidate, 100x out of line.
        rows = [fact("SharesOutstanding", f"FY{y}", 40000.0 + y,
                     file=f"X.NZ_Annual_FY{y}.txt") for y in (2021, 2022, 2023)]
        rows.append(fact("SharesOutstanding", "H1-2020", 358.0,
                         file="X.NZ_HalfYear_H1-2020.txt"))
        ps = adjudicate.propose(db(rows))
        p = cell(ps, "shares_outstanding", "H1 FY2020")
        assert p.status == "contested"
        assert "out of line" in p.rationale
        assert cell(ps, "shares_outstanding", "FY2022").status == "resolved"

    def test_outlier_guard_needs_enough_history(self, db):
        rows = [fact("Revenue", "FY2023", 40000.0, file="X.NZ_Annual_FY2023.txt"),
                fact("Revenue", "FY2024", 358.0)]
        assert cell(adjudicate.propose(db(rows)), "revenue", "FY2024").status == "resolved"

    def test_worksheet_tags_section(self, db):
        con = db([fact("Revenue", "FY2024", 1.0, line=20),
                  fact("Revenue", "FY2024", 2.0, line=60),
                  fact("Revenue", "FY2024", 3.0, line=70)])
        text = adjudicate.worksheet("X.NZ", adjudicate.propose(con, self.secs()), {})
        assert "[stmt/statement]" in text
        assert "[stmt/summary]" in text


class TestJudgmentMetrics:
    def test_definition_sensitive_metric_is_flagged_not_green(self, db):
        # SEK.NZ capex: the scanner's "purchase of PP&E" line is right as a
        # line and wrong as capex (the agent adds intangibles). Resolved, but
        # shown as ~ with every distinct statement value, never as ✓.
        con = db([fact("CapEx", "FY2024", -12917.0, line=1364),
                  fact("CapEx", "FY2024", -12917.0, line=1400, context="Note 12")])
        ps = adjudicate.propose(con)
        p = cell(ps, "capex", "FY2024")
        assert (p.status, p.flag) == ("resolved", "confirm-definition")
        text = adjudicate.worksheet("X.NZ", ps, {})
        grid = text.split("## Grid")[1].split("## Resolved")[0]
        assert "~" in grid
        assert "✓" not in grid
        assert "## Confirm definition" in text
        assert "capex FY2024" in text.split("## Confirm definition")[1]

    def test_definition_list_omits_guarded_candidates(self, db):
        # 0006.HK net_income: the dash row (0) was excluded from the pick but
        # still led the "other distinct values" list.
        con = db([fact("NetIncome", "FY2024", 0.0, line=10),
                  fact("NetIncome", "FY2024", 6119.0, line=60),
                  fact("NetIncome", "FY2024", 6119.0, line=900, context="Note 9")])
        p = cell(adjudicate.propose(con), "net_income", "FY2024")
        assert (p.status, p.value_raw) == ("resolved", 6119.0)
        assert [c.value_raw for c in p.shortlist] == [6119.0]

    def test_check_reports_precision_without_judgment_metrics(self, db):
        con = db([fact("Revenue", "FY2024", 263527.0),
                  fact("CapEx", "FY2024", -12917.0)])
        con.execute("INSERT INTO core_metrics (period, revenue, capex, units)"
                    " VALUES ('FY2024', 263.527, 18.296, 'millions')")
        rep = adjudicate.check(con, adjudicate.propose(con))
        assert (rep["compared"], rep["agree"]) == (2, 1)
        assert rep["firm"] == (1, 1)          # (agree, compared) excluding ~ cells
        assert rep["firm_value"] == (1, 1)


class TestVocabulary:
    def test_undated_rows_are_skipped_and_odd_labels_kept(self, db):
        # A None period (undated comparative) is not a cell; a label the period
        # parser cannot date is kept verbatim rather than dropped.
        con = db([fact("Revenue", None, 9.0, confidence="prior_year_column"),
                  fact("Revenue", "FY2024", None),
                  fact("Revenue", "Stub", 3.0, file="X.NZ_Other.txt")])
        ps = adjudicate.propose(con)
        assert {(p.metric, p.period, p.status) for p in ps} == {
            ("revenue", "Stub", "resolved"), ("revenue", "FY2024", "missing")}

    def test_contested_kpi_is_listed(self, db):
        con = db([fact("InterestIncome", "FY2024", 0.4, line=130),
                  fact("InterestIncome", "FY2024", 0.9, line=700, context="Note 6")])
        ps = adjudicate.propose(con)
        text = adjudicate.worksheet("X.NZ", ps, {})
        assert "- InterestIncome FY2024" in text.split("## KPIs")[1]

    def test_period_spellings_merge(self, db):
        con = db([fact("Revenue", "H1-2024", 5.0, file="X.NZ_HalfYear_H1-2024.txt"),
                  fact("Revenue", "H1 FY2024", 5.0, file="X.NZ_Annual_FY2025.txt",
                       confidence="prior_year_column")])
        ps = [p for p in adjudicate.propose(con)
              if p.metric == "revenue" and p.status != "missing"]
        assert [p.period for p in ps] == ["H1 FY2024"]

    def test_non_core_metric_goes_to_kpis(self, db):
        con = db([fact("Revenue", "FY2024", 1.0),
                  fact("InterestIncome", "FY2024", 0.4)])
        ps = adjudicate.propose(con)
        k = cell(ps, "InterestIncome", "FY2024")
        assert k.kind == "kpi"
        assert k.status == "resolved"
        assert cell(ps, "revenue", "FY2024").kind == "core"

    def test_metric_never_extracted_has_no_cells(self, db):
        con = db([fact("Revenue", "FY2024", 1.0)])
        assert {p.metric for p in adjudicate.propose(con)} == {"revenue"}


class TestWorksheet:
    def rows(self):
        return [fact("Revenue", "FY2024", 263527.0),
                fact("Revenue", "FY2023", 250000.0, file="X.NZ_Annual_FY2023.txt"),
                fact("NetIncome", "FY2024", 9.0, line=120),
                fact("NetIncome", "FY2024", 7.0, line=950, context="Note 9"),
                fact("TotalDebt", "FY2023", 40.0, file="X.NZ_Annual_FY2023.txt"),
                fact("InterestIncome", "FY2024", 0.4, line=130)]

    def test_grid_and_sections(self, db):
        ps = adjudicate.propose(db(self.rows()))
        text = adjudicate.worksheet("X.NZ", ps, {})
        grid = text.split("## Contested")[0]
        assert "| FY2023 |" in grid
        assert "| FY2024 |" in grid
        assert "revenue" in grid
        assert "total_debt" in grid
        assert "✓" in grid
        assert "?" in grid
        assert "✗" in grid
        assert "## Resolved" in text
        assert "263527" in text
        assert "## Contested" in text
        assert "Note 9" in text
        assert "## Missing" in text
        assert "total_debt" in text
        assert "## KPIs" in text
        assert "InterestIncome" in text
        assert "## Filings" in text
        assert "thousands" in text

    def test_missing_pointers_are_printed(self, db):
        ps = adjudicate.propose(db(self.rows()))
        pointers = {"X.NZ_Annual_FY2024.txt": [("Statement of financial position", 300, 360)]}
        text = adjudicate.worksheet("X.NZ", ps, pointers)
        assert "X.NZ_Annual_FY2024.txt:300-360" in text

    def test_worksheet_size_budget(self, db):
        rows = [fact(m, f"FY{2006 + i}", float(i * 7 + j), line=j * 10 + 1,
                     file=f"X.NZ_Annual_FY{2006 + i}.txt",
                     context="Consolidated statement x " * 10)
                for i in range(20) for m in ("Revenue", "NetIncome", "TotalAssets",
                                             "CapEx", "OperatingCashFlow", "EBITDA",
                                             "TotalDebt", "CashAndEquivalents",
                                             "SharesOutstanding", "GrossProfit",
                                             "InterestIncome", "DividendsPaid")
                for j in range(6)]
        text = adjudicate.worksheet("X.NZ", adjudicate.propose(db(rows)), {})
        assert len(text.encode()) <= adjudicate.SIZE_BUDGET


class TestTable:
    def test_write_is_idempotent(self, db):
        con = db([fact("Revenue", "FY2024", 1.0), fact("NetIncome", "FY2024", 2.0)])
        ps = adjudicate.propose(con)
        adjudicate.write_table(con, ps)
        adjudicate.write_table(con, ps)
        rows = con.execute(
            "SELECT metric, period, value_raw, status, rung FROM proposed_metrics"
            " ORDER BY metric").fetchall()
        assert rows == [("net_income", "FY2024", 2.0, "resolved", "single"),
                        ("revenue", "FY2024", 1.0, "resolved", "single")]
        assert con.execute("SELECT count(*) FROM core_metrics").fetchone()[0] == 0


class TestCheck:
    def test_precision_against_core_metrics(self, db):
        con = db([fact("Revenue", "FY2024", 263527.0),          # thousands
                  fact("NetIncome", "FY2024", 9100.0),
                  fact("TotalDebt", "FY2024", 5.0)])             # wrong
        con.execute("INSERT INTO core_metrics (period, revenue, net_income, total_debt,"
                    " units, currency) VALUES ('FY2024', 263.527, 9.1, 50.0,"
                    " 'millions', 'NZD')")
        rep = adjudicate.check(con, adjudicate.propose(con))
        assert rep["resolved"] == 3
        assert rep["compared"] == 3
        assert rep["agree"] == 2
        assert rep["by_rung"]["single"] == (2, 3)
        assert ("total_debt", "FY2024") in rep["disagreements"]

    def test_agreement_tolerates_rounding_sign_and_per_share_scale(self, db):
        con = db([fact("CapEx", "FY2024", -4752.0),        # agent wrote +4.8
                  fact("EPS", "FY2024", -3.9),             # cents vs dollars
                  fact("Revenue", "FY2024", 263527.0),
                  fact("NetIncome", "FY2024", 9100.0)])    # agent wrote 9.5: wrong
        con.execute("INSERT INTO core_metrics (period, capex, eps, revenue, net_income,"
                    " units) VALUES ('FY2024', 4.8, -0.039, 263.5, 9.5, 'millions')")
        rep = adjudicate.check(con, adjudicate.propose(con))
        assert (rep["compared"], rep["agree"]) == (4, 3)
        assert list(rep["disagreements"]) == [("net_income", "FY2024")]

    def test_disagreements_are_classified(self, db):
        con = db([fact("Revenue", "H1-2020", 9.0, file="X.NZ_HalfYear_H1-2020.txt"),
                  fact("Revenue", "H1-2019", 7.0, file="X.NZ_HalfYear_H1-2019.txt"),
                  fact("NetIncome", "FY2024", 5.0)])
        # the agent labelled half-years one fiscal year later than the filenames
        con.execute("INSERT INTO core_metrics (period, revenue) VALUES"
                    " ('H1 FY2021', 9.0), ('H1 FY2020', 7.0)")
        con.execute("INSERT INTO core_metrics (period, net_income) VALUES ('FY2024', 50.0)")
        rep = adjudicate.check(con, adjudicate.propose(con))
        assert rep["why"][("revenue", "H1 FY2020")] == "period-shift"
        assert rep["why"][("net_income", "FY2024")] == "other"

    def test_legacy_core_metrics_without_newer_columns(self, db):
        # Tickers researched before the schema grew have a narrower table;
        # grading must use the columns that exist, not the current DDL.
        con = db([fact("Revenue", "FY2024", 263527.0)])
        con.execute("DROP TABLE core_metrics")
        con.execute("CREATE TABLE core_metrics (period TEXT, revenue DOUBLE, units TEXT)")
        con.execute("INSERT INTO core_metrics VALUES ('FY2024', 263.527, 'millions')")
        rep = adjudicate.check(con, adjudicate.propose(con))
        assert (rep["compared"], rep["agree"]) == (1, 1)

    def test_unknown_units_fall_back_to_any_decade(self, db):
        con = db([fact("Revenue", "FY2024", 263527.0, units=None),
                  fact("NetIncome", "FY2024", 9.0, units=None)])
        con.execute("INSERT INTO core_metrics (period, revenue, net_income)"
                    " VALUES ('FY2024', 263.527, 0.9)")
        rep = adjudicate.check(con, adjudicate.propose(con))
        assert rep["agree"] == 1
        assert rep["compared"] == 2
        assert ("net_income", "FY2024") in rep["disagreements"]

    def test_unresolved_cells_are_not_compared(self, db):
        con = db([fact("Revenue", "FY2024", 1.0, line=1),
                  fact("Revenue", "FY2024", 2.0, line=2)])
        con.execute("INSERT INTO core_metrics (period, revenue) VALUES ('FY2024', 1.0)")
        rep = adjudicate.check(con, adjudicate.propose(con))
        assert rep["compared"] == 0


class TestCli:
    def test_writes_worksheet_and_table(self, make_ticker, monkeypatch, capsys):
        import duckdb
        import schema

        d = make_ticker("SYN.NZ")
        monkeypatch.setattr(adjudicate, "REPO", d.parents[1])
        (d / "Extracted" / "SYN.NZ_Annual_FY2024.txt").write_text(
            "Consolidated statement of comprehensive income\nRevenue 263,527 250,000\n")
        con = duckdb.connect(str(d / "Reports" / "SYN.NZ.duckdb"))
        con.execute(schema.create_sql())
        con.executemany(
            f"INSERT INTO facts ({', '.join(FACT_COLS)}) VALUES "
            f"({', '.join('?' * len(FACT_COLS))})",
            [fact("Revenue", "FY2024", 263527.0, file="SYN.NZ_Annual_FY2024.txt")])
        con.close()

        monkeypatch.setattr(sys, "argv", ["adjudicate.py", "SYN.NZ"])
        assert adjudicate.main() == 0
        ws = d / "Reports" / "SYN.NZ_Worksheet.md"
        assert ws.exists()
        assert "263527" in ws.read_text()
        out = capsys.readouterr().out
        assert "1 resolved" in out
        assert "Worksheet" in out

    def test_check_flag_prints_grades(self, make_ticker, monkeypatch, capsys):
        import duckdb
        import schema

        d = make_ticker("SYN.NZ")
        monkeypatch.setattr(adjudicate, "REPO", d.parents[1])
        con = duckdb.connect(str(d / "Reports" / "SYN.NZ.duckdb"))
        con.execute(schema.create_sql())
        con.executemany(
            f"INSERT INTO facts ({', '.join(FACT_COLS)}) VALUES "
            f"({', '.join('?' * len(FACT_COLS))})",
            [fact("Revenue", "FY2024", 263527.0, file="SYN.NZ_Annual_FY2024.txt"),
             fact("TotalDebt", "FY2024", 5.0, file="SYN.NZ_Annual_FY2024.txt")])
        con.execute("INSERT INTO core_metrics (period, revenue, total_debt, units)"
                    " VALUES ('FY2024', 263.527, 50.0, 'millions')")
        con.close()
        monkeypatch.setattr(sys, "argv", ["adjudicate.py", "SYN.NZ", "--check"])
        assert adjudicate.main() == 0
        out = capsys.readouterr().out
        assert "1/2 resolved cells agree" in out
        assert "✗ total_debt FY2024: proposed 5, core has 50" in out

    def test_empty_facts_table_is_a_clean_failure(self, make_ticker, monkeypatch, capsys):
        import duckdb
        import schema

        d = make_ticker("EMPTY.NZ")
        monkeypatch.setattr(adjudicate, "REPO", d.parents[1])
        con = duckdb.connect(str(d / "Reports" / "EMPTY.NZ.duckdb"))
        con.execute(schema.create_sql())
        con.close()
        monkeypatch.setattr(sys, "argv", ["adjudicate.py", "EMPTY.NZ"])
        assert adjudicate.main() == 2
        assert "no facts rows" in capsys.readouterr().err

    def test_missing_db_is_a_clean_failure(self, make_ticker, monkeypatch, capsys):
        d = make_ticker("NOPE.NZ")
        monkeypatch.setattr(adjudicate, "REPO", d.parents[1])
        monkeypatch.setattr(sys, "argv", ["adjudicate.py", "NOPE.NZ"])
        assert adjudicate.main() == 2
        assert "no facts" in capsys.readouterr().err.lower()
