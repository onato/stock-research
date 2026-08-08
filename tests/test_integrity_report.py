"""integrity_report.py measures what financial data the corpus actually has.

The goal it serves is 10 years of complete history per company. Three things
make that hard to measure, and each is pinned by tests here:

* The committed CSV -- not the DuckDB -- is the system of record. The .duckdb
  files are gitignored rebuildable caches and several have drifted badly
  (BABA's DB reads 4.8% filled against a 96.5%-filled CSV). Scoring the DBs
  would invent catastrophic gaps that do not exist, so the CSV is measured
  and DB drift is reported as its own signal.
* "Complete" means the eight fields a DCF needs, not every column. Company
  KPI columns (FacebookMAU, VehicleSales) are ~10% filled by nature and must
  not drag a ticker's score down.
* Header spellings drift (EPSDiluted, FCF, Equity), so completeness resolves
  through schema.normalize() rather than exact-matching column names.
"""

import csv
import json
import sys

import duckdb
import integrity_report
import pytest
import schema

CORE8_HEADERS = ["Revenue", "NetIncome", "EPS", "OperatingCashFlow",
                 "CapEx", "FreeCashFlow", "ShareholdersEquity",
                 "SharesOutstanding"]


def write_csv(repo, ticker, rows, headers=None):
    """Write research/{T}/Reports/{T}_Metrics.csv from dict rows."""
    d = repo / "research" / ticker / "Reports"
    d.mkdir(parents=True, exist_ok=True)
    headers = headers or ["Period", *CORE8_HEADERS, "Units", "Currency"]
    with open(d / f"{ticker}_Metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def full_year(period, **over):
    """A ticker-year with all eight core fields populated."""
    row = {"Period": period, "Units": "millions", "Currency": "USD"}
    for h in CORE8_HEADERS:
        row[h] = "1.0"
    row.update(over)
    return row


def write_filings(repo, ticker, n=3):
    d = repo / "research" / ticker / "Extracted"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"{ticker}_Annual_FY{2020 + i}.txt").write_text("filing text")


def write_db(repo, ticker, periods):
    """Create the ticker's DuckDB cache with every core-8 column populated.

    A healthy cache mirrors its CSV, so the default fixture fills all eight
    fields; tests that want a drifted cache build one explicitly.
    """
    d = repo / "research" / ticker / "Reports"
    d.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(d / f"{ticker}.duckdb"))
    con.execute(schema.create_sql())
    cols = ", ".join(integrity_report.CORE8)
    marks = ", ".join("?" * len(integrity_report.CORE8))
    for period in periods:
        con.execute(
            f"INSERT INTO core_metrics (period, {cols}, units, currency)"
            f" VALUES (?, {marks}, 'millions', 'USD')",
            [period, *([1.0] * len(integrity_report.CORE8))])
    con.close()


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "research").mkdir()
    (tmp_path / "state").mkdir()
    return tmp_path


def scan_one(repo, ticker):
    """Scan the tmp corpus and return the single record for `ticker`."""
    recs = {r["ticker"]: r for r in integrity_report.scan(repo)}
    return recs[ticker]


class TestCompleteness:
    def test_all_eight_core_fields_makes_a_year_complete(self, repo):
        write_csv(repo, "NFLX", [full_year("FY2024")])
        assert scan_one(repo, "NFLX")["complete_years"] == 1

    def test_one_missing_core_field_makes_the_year_incomplete(self, repo):
        write_csv(repo, "NFLX", [full_year("FY2024", FreeCashFlow="")])
        rec = scan_one(repo, "NFLX")
        assert rec["complete_years"] == 0
        assert rec["fy_years"] == 1  # the year still exists, just not complete

    def test_aliased_headers_resolve_through_schema_normalize(self, repo):
        # A CSV spelling EPS as EPSDiluted and FCF as FCF is complete: the
        # data is there, only the header drifted. Exact-matching would call
        # this a gap and send someone re-parsing a file that is already fine.
        headers = ["Period", "Revenue", "NetIncome", "EPSDiluted",
                   "CashFromOperations", "CapitalExpenditure", "FCF",
                   "Equity", "SharesOutstanding", "Units", "Currency"]
        row = dict.fromkeys(headers, "1.0")
        row.update({"Period": "FY2024", "Units": "millions", "Currency": "USD"})
        write_csv(repo, "ALIAS", [row], headers=headers)
        assert scan_one(repo, "ALIAS")["complete_years"] == 1

    def test_unit_suffixed_headers_are_still_core_metrics(self, repo):
        # BABA and 9988.HK spell every column with its scale baked in
        # (Revenue_RMB_Mn, EPS_Diluted_RMB); AGL.NZ uses EPS_cents and
        # SharesOutstanding_m. The numbers are all present -- only the header
        # carries a unit suffix. Treating these as unrecognised KPIs reported
        # BABA as "100% filled, 0 complete years", which reads as a broken
        # score and would send someone re-parsing a correct file.
        headers = ["Period", "Revenue_RMB_Mn", "NetIncome_RMB_Mn",
                   "EPS_Diluted_RMB", "OperatingCashFlow_RMB_Mn",
                   "CapEx_RMB_Mn", "FreeCashFlow_RMB_Mn",
                   "ShareholdersEquity_RMB_Mn", "SharesOutstanding_Mn"]
        row = dict.fromkeys(headers, "1.0")
        row["Period"] = "FY2024"
        write_csv(repo, "BABA", [row], headers=headers)
        assert scan_one(repo, "BABA")["complete_years"] == 1

    def test_cents_and_millions_suffixes_resolve(self, repo):
        headers = ["Period", "Revenue", "NetIncome", "EPS_cents",
                   "OperatingCashFlow", "CapEx", "FreeCashFlow",
                   "ShareholdersEquity", "SharesOutstanding_m"]
        row = dict.fromkeys(headers, "1.0")
        row["Period"] = "FY2024"
        write_csv(repo, "AGL.NZ", [row], headers=headers)
        assert scan_one(repo, "AGL.NZ")["complete_years"] == 1

    def test_a_segment_revenue_kpi_is_not_mistaken_for_revenue(self, repo):
        # The suffix rule must not swallow genuine KPIs: AWSRevenue and
        # iPhoneRevenue are segment lines, not the company's revenue. If they
        # mapped to `revenue` a ticker could look complete on segment data
        # alone.
        headers = ["Period", "AWSRevenue", "iPhoneRevenue",
                   "SubscriptionRevenue"]
        row = dict.fromkeys(headers, "1.0")
        row["Period"] = "FY2024"
        write_csv(repo, "AMZN", [row], headers=headers)
        rec = scan_one(repo, "AMZN")
        assert rec["complete_years"] == 0
        assert "Revenue" not in rec["per_field_fill"]

    def test_company_kpi_columns_do_not_count_against_fill(self, repo):
        # FacebookMAU-style columns are ~10% filled by nature. A ticker with
        # every core field present is 100% filled even if its KPI column is
        # empty for that year.
        headers = ["Period", *CORE8_HEADERS, "FacebookMAU", "Units", "Currency"]
        write_csv(repo, "META", [full_year("FY2024")], headers=headers)
        rec = scan_one(repo, "META")
        assert rec["complete_years"] == 1
        assert rec["cell_fill_pct"] == 100.0
        assert "FacebookMAU" not in rec["per_field_fill"]

    def test_only_fy_periods_count_toward_year_depth(self, repo):
        # Half-year and quarterly rows are real data but are not extra years
        # of history; counting them would overstate depth against the goal.
        write_csv(repo, "PINS", [
            full_year("FY2024"), full_year("H1-2024"), full_year("Q3-2024"),
        ])
        rec = scan_one(repo, "PINS")
        assert rec["fy_years"] == 1
        assert rec["complete_years"] == 1

    def test_duplicate_fy_rows_count_once(self, repo):
        write_csv(repo, "DUP", [full_year("FY2024"), full_year("FY2024")])
        assert scan_one(repo, "DUP")["fy_years"] == 1

    def test_year_span_is_reported(self, repo):
        write_csv(repo, "NFLX",
                  [full_year(f"FY{y}") for y in range(2015, 2025)])
        rec = scan_one(repo, "NFLX")
        assert rec["first_year"] == 2015
        assert rec["latest_year"] == 2024

    def test_whitespace_only_cell_is_missing_not_present(self, repo):
        write_csv(repo, "WS", [full_year("FY2024", Revenue="   ")])
        assert scan_one(repo, "WS")["complete_years"] == 0

    def test_empty_csv_does_not_crash(self, repo):
        write_csv(repo, "BARE", [])
        rec = scan_one(repo, "BARE")
        assert rec["fy_years"] == 0
        assert rec["cell_fill_pct"] == 0.0


class TestTenYearGoal:
    def test_ten_complete_years_meets_the_goal(self, repo):
        write_csv(repo, "GOOD",
                  [full_year(f"FY{y}") for y in range(2015, 2025)])
        rec = scan_one(repo, "GOOD")
        assert rec["complete_years"] == 10
        assert rec["stage"] == "complete-10yr"

    def test_nine_complete_years_falls_short(self, repo):
        # The boundary is the whole point of the dashboard; off-by-one here
        # would misreport how close the corpus is to the goal.
        write_csv(repo, "NEAR",
                  [full_year(f"FY{y}") for y in range(2016, 2025)])
        rec = scan_one(repo, "NEAR")
        assert rec["complete_years"] == 9
        assert rec["stage"] != "complete-10yr"

    def test_ten_years_present_but_incomplete_does_not_meet_the_goal(self, repo):
        # Ten years of rows where each is missing FCF is not ten years of
        # usable data.
        write_csv(repo, "THIN", [full_year(f"FY{y}", FreeCashFlow="")
                                 for y in range(2015, 2025)])
        rec = scan_one(repo, "THIN")
        assert rec["fy_years"] == 10
        assert rec["complete_years"] == 0
        assert rec["stage"] != "complete-10yr"


class TestFunnel:
    def test_no_filings_is_empty(self, repo):
        (repo / "research" / "NEW.HK").mkdir()
        assert scan_one(repo, "NEW.HK")["stage"] == "empty"

    def test_filings_without_a_csv_is_the_parse_backlog(self, repo):
        write_filings(repo, "0001.HK", n=9)
        rec = scan_one(repo, "0001.HK")
        assert rec["stage"] == "filings-only"
        assert rec["n_extracted"] == 9
        assert rec["has_filings"] is True
        assert rec["has_csv"] is False

    def test_csv_without_a_dcf_is_parsed(self, repo):
        write_filings(repo, "SEK.NZ")
        write_csv(repo, "SEK.NZ", [full_year("FY2024")])
        assert scan_one(repo, "SEK.NZ")["stage"] == "parsed"

    def test_csv_with_a_dcf_is_valued(self, repo):
        write_filings(repo, "SUM.NZ")
        write_csv(repo, "SUM.NZ", [full_year("FY2024")])
        d = repo / "research" / "SUM.NZ" / "Reports"
        (d / "SUM.NZ_DCF.json").write_text("{}")
        assert scan_one(repo, "SUM.NZ")["stage"] == "valued"

    def test_summary_counts_the_whole_funnel(self, repo):
        (repo / "research" / "EMPTY.HK").mkdir()
        write_filings(repo, "0001.HK")
        write_filings(repo, "SEK.NZ")
        write_csv(repo, "SEK.NZ", [full_year("FY2024")])
        write_filings(repo, "NFLX")
        write_csv(repo, "NFLX", [full_year(f"FY{y}") for y in range(2015, 2025)])
        (repo / "research" / "NFLX" / "Reports" / "NFLX_DCF.json").write_text("{}")

        s = integrity_report.summarize(integrity_report.scan(repo))
        assert s["tracked"] == 4
        assert s["with_filings"] == 3
        assert s["parsed"] == 2
        assert s["complete_10yr"] == 1


class TestExchange:
    def test_suffix_after_the_dot_is_the_exchange(self, repo):
        write_csv(repo, "AGL.NZ", [full_year("FY2024")])
        assert scan_one(repo, "AGL.NZ")["exchange"] == "NZ"

    def test_bare_ticker_is_us(self, repo):
        # 55 US tickers carry no suffix; defaulting them to "" would create a
        # phantom exchange bucket in the backlog chart.
        write_csv(repo, "NFLX", [full_year("FY2024")])
        assert scan_one(repo, "NFLX")["exchange"] == "US"

    def test_backlog_is_grouped_by_exchange(self, repo):
        write_filings(repo, "0001.HK")
        write_filings(repo, "0002.HK")
        write_filings(repo, "AGL.NZ")
        write_csv(repo, "AGL.NZ", [full_year("FY2024")])
        s = integrity_report.summarize(integrity_report.scan(repo))
        by = {e["exchange"]: e for e in s["by_exchange"]}
        assert by["HK"]["filings_only"] == 2
        assert by["HK"]["parsed"] == 0
        assert by["NZ"]["parsed"] == 1


class TestDuckDBDrift:
    def test_missing_db_is_flagged_not_fatal(self, repo):
        write_csv(repo, "NFLX", [full_year("FY2024")])
        assert scan_one(repo, "NFLX")["db_status"] == "DB_MISSING"

    def test_db_matching_the_csv_is_ok(self, repo):
        write_csv(repo, "NFLX", [full_year(f"FY{y}") for y in (2023, 2024)])
        write_db(repo, "NFLX", ["FY2023", "FY2024"])
        assert scan_one(repo, "NFLX")["db_status"] == "DB_OK"

    def test_db_with_fewer_years_than_the_csv_is_stale(self, repo):
        # The BABA case: a half-adjudicated cache behind its own CSV. This is
        # the signal that says "rebuild", and it must not be silent.
        write_csv(repo, "BABA",
                  [full_year(f"FY{y}") for y in range(2015, 2025)])
        write_db(repo, "BABA", ["FY2024"])
        assert scan_one(repo, "BABA")["db_status"] == "DB_STALE"

    def test_db_with_no_fy_rows_is_stale(self, repo):
        write_csv(repo, "FRFHF",
                  [full_year(f"FY{y}") for y in range(2015, 2025)])
        write_db(repo, "FRFHF", [])
        assert scan_one(repo, "FRFHF")["db_status"] == "DB_STALE"

    def test_db_with_the_right_years_but_empty_cells_is_stale(self, repo):
        # Year count alone is not enough: BABA's cache carried FY rows whose
        # core columns were almost entirely NULL (4.8% filled against a
        # complete CSV). Counting rows would call that healthy.
        write_csv(repo, "BABA", [full_year(f"FY{y}") for y in (2023, 2024)])
        d = repo / "research" / "BABA" / "Reports"
        con = duckdb.connect(str(d / "BABA.duckdb"))
        con.execute(schema.create_sql())
        for period in ("FY2023", "FY2024"):
            con.execute("INSERT INTO core_metrics (period, units, currency)"
                        " VALUES (?, 'millions', 'USD')", [period])
        con.close()
        assert scan_one(repo, "BABA")["db_status"] == "DB_STALE"

    def test_unreadable_db_degrades_rather_than_raising(self, repo):
        # A corrupt cache must never take down the whole report.
        write_csv(repo, "JUNK", [full_year("FY2024")])
        d = repo / "research" / "JUNK" / "Reports"
        (d / "JUNK.duckdb").write_bytes(b"not a database")
        assert scan_one(repo, "JUNK")["db_status"] == "DB_UNREADABLE"

    def test_db_without_core_metrics_is_unreadable_not_a_crash(self, repo):
        write_csv(repo, "OLD.NZ", [full_year("FY2024")])
        d = repo / "research" / "OLD.NZ" / "Reports"
        con = duckdb.connect(str(d / "OLD.NZ.duckdb"))
        con.execute("CREATE TABLE unrelated (x INTEGER)")
        con.close()
        assert scan_one(repo, "OLD.NZ")["db_status"] == "DB_UNREADABLE"


class TestPerFieldFill:
    def test_weakest_fields_are_reported_across_the_corpus(self, repo):
        # This is the extraction to-do list: which metric is worth teaching
        # the parser next.
        write_csv(repo, "A", [full_year("FY2024", CapEx="")])
        write_csv(repo, "B", [full_year("FY2024", CapEx="")])
        s = integrity_report.summarize(integrity_report.scan(repo))
        fill = {f["field"]: f["pct"] for f in s["field_fill"]}
        assert fill["CapEx"] == 0.0
        assert fill["Revenue"] == 100.0
        # worst-first ordering drives the chart
        assert s["field_fill"][0]["field"] == "CapEx"


class TestDepthChart:
    def test_long_tail_is_binned_so_the_goal_stays_readable(self, repo):
        # NFLX has 20 FY years and the next-deepest ticker has 16, which
        # stretched the axis so far that the 3-13 year bars -- where every
        # actionable ticker sits -- were squeezed into a corner.
        svg = integrity_report.depth_chart(
            [(3, 1), (10, 15), (11, 22), (16, 1), (20, 1)], 10)
        assert "15+" in svg
        # the two outliers merge into one overflow bar
        assert svg.count("<rect") == 4

    def test_bars_at_the_goal_are_highlighted(self, repo):
        svg = integrity_report.depth_chart([(9, 1), (10, 1)], 10)
        assert integrity_report.STAGE_COLORS["complete-10yr"] in svg
        assert integrity_report.STAGE_COLORS["parsed"] in svg

    def test_empty_depth_does_not_crash(self, repo):
        assert "No parsed tickers" in integrity_report.depth_chart([], 10)


class TestRender:
    def test_html_has_no_external_references(self, repo):
        # Every dashboard in this repo opens from file:// with no server.
        write_csv(repo, "NFLX", [full_year("FY2024")])
        recs = integrity_report.scan(repo)
        html = integrity_report.render(recs, integrity_report.summarize(recs), {})
        assert "http://" not in html
        assert "https://" not in html
        assert "<script src=" not in html

    def test_html_reports_the_goal_metric(self, repo):
        write_csv(repo, "NFLX", [full_year(f"FY{y}") for y in range(2015, 2025)])
        recs = integrity_report.scan(repo)
        html = integrity_report.render(recs, integrity_report.summarize(recs), {})
        assert "NFLX" in html
        assert "10" in html

    def test_ticker_names_are_html_escaped(self, repo):
        recs = [{"ticker": "<img src=x>", "stage": "empty", "exchange": "US",
                 "fy_years": 0, "complete_years": 0, "cell_fill_pct": 0.0,
                 "latest_year": None, "first_year": None, "db_status": "DB_MISSING",
                 "has_filings": False, "has_csv": False, "n_extracted": 0,
                 "has_dcf": False, "has_dashboard": False, "has_duckdb": False,
                 "per_field_fill": {}, "missing_fields": []}]
        html = integrity_report.render(recs, integrity_report.summarize(recs), {})
        assert "<img src=x>" not in html

    def test_company_names_come_from_the_companies_map(self, repo):
        write_csv(repo, "NFLX", [full_year("FY2024")])
        recs = integrity_report.scan(repo)
        companies = {"NFLX": {"name": "Netflix, Inc.", "sector": "Streaming"}}
        html = integrity_report.render(
            recs, integrity_report.summarize(recs), companies)
        assert "Netflix, Inc." in html


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["integrity_report.py", *argv])
    return integrity_report.main()


class TestMain:
    def test_writes_json_and_html(self, repo, monkeypatch, capsys):
        write_filings(repo, "0001.HK")
        write_csv(repo, "NFLX", [full_year(f"FY{y}") for y in range(2015, 2025)])
        out_json = repo / "state" / "integrity.json"
        out_html = repo / "integrity.html"
        assert run(monkeypatch, "--root", str(repo),
                   "--json", str(out_json), "--html", str(out_html)) == 0

        data = json.loads(out_json.read_text())
        assert data["summary"]["tracked"] == 2
        assert data["summary"]["complete_10yr"] == 1
        assert out_html.read_text().startswith("<!DOCTYPE html>")
        assert "2 tickers" in capsys.readouterr().out

    def test_missing_research_dir_errors_cleanly(self, tmp_path, monkeypatch, capsys):
        assert run(monkeypatch, "--root", str(tmp_path)) == 1
        assert "no research/" in capsys.readouterr().err
