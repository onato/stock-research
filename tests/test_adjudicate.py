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

    def test_only_comparative_evidence_is_contested(self, db):
        # No filing of FY2023's own, but FY2024's comparative column has it.
        # Worth showing the agent; not strong enough to resolve alone.
        con = db([fact("Revenue", "FY2024", 2.0),
                  fact("Revenue", "FY2023", 1.0, confidence="prior_year_column")])
        p = cell(adjudicate.propose(con), "revenue", "FY2023")
        assert p.status == "contested"
        assert p.shortlist[0].confidence == "prior_year_column"


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
        assert "### InterestIncome FY2024" in text.split("## KPIs")[1]

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
                                             "CapEx", "OperatingCashFlow")
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
