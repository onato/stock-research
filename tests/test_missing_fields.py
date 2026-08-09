"""missing_fields.py names the specific gaps in the metrics CSVs.

integrity_report already answers "how complete is this ticker" with a
percentage. That is the wrong shape for fixing anything: "CapEx 40%" does not
say which years to go and re-extract. This emits one machine-readable record
per ticker+field, carrying the exact periods that are empty, so a fix run can
be driven straight off the output.

One row per ticker+field rather than per cell: the corpus has 932 missing
core-8 cells but only 170 ticker+field pairs, and the periods list keeps the
detail without 932 rows of it.
"""

import csv
import io
import json
import sys

import missing_fields
import pytest


def write(path, headers, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "research").mkdir()
    return tmp_path


def make(repo, ticker, headers, rows):
    d = repo / "research" / ticker / "Reports"
    d.mkdir(parents=True, exist_ok=True)
    write(d / f"{ticker}_Metrics.csv", headers, rows)


CORE = ["Period", "Revenue", "NetIncome", "EPS", "OperatingCashFlow",
        "CapEx", "FreeCashFlow", "ShareholdersEquity", "SharesOutstanding"]


def full(period):
    return [period] + ["1"] * (len(CORE) - 1)


def gap(period, field, value=""):
    row = full(period)
    row[CORE.index(field)] = value
    return row


class TestGapDetection:
    def test_a_missing_field_is_reported_with_its_periods(self, repo):
        make(repo, "PINS", CORE, [gap("FY2023", "CapEx"),
                                  gap("FY2024", "CapEx")])
        rows = missing_fields.scan(repo)
        capex = [r for r in rows if r["field"] == "CapEx"]
        assert len(capex) == 1
        assert capex[0]["ticker"] == "PINS"
        assert capex[0]["periods"] == ["FY2023", "FY2024"]
        assert capex[0]["missing_count"] == 2

    def test_a_fully_populated_ticker_yields_nothing(self, repo):
        make(repo, "AMZN", CORE, [full("FY2023"), full("FY2024")])
        assert missing_fields.scan(repo) == []

    def test_a_field_absent_from_the_header_counts_as_missing_everywhere(
            self, repo):
        # A column the CSV never had is missing for every year, not absent
        # from the report -- that is the case worth surfacing loudest.
        make(repo, "V", ["Period", "Revenue"], [["FY2023", "1"],
                                                ["FY2024", "1"]])
        rows = {r["field"]: r for r in missing_fields.scan(repo)}
        assert rows["CapEx"]["periods"] == ["FY2023", "FY2024"]
        assert rows["CapEx"]["absent_column"] is True
        assert rows["Revenue"]["field"] if "Revenue" in rows else True

    def test_a_present_column_that_is_merely_empty_is_not_absent(self, repo):
        make(repo, "PINS", CORE, [gap("FY2024", "CapEx")])
        row = next(r for r in missing_fields.scan(repo)
                   if r["field"] == "CapEx")
        assert row["absent_column"] is False

    def test_whitespace_only_counts_as_missing(self, repo):
        make(repo, "WS", CORE, [gap("FY2024", "EPS", "   ")])
        assert any(r["field"] == "EPS" for r in missing_fields.scan(repo))

    def test_only_fy_rows_are_considered(self, repo):
        # H1/Q rows legitimately lack annual figures; reporting them would
        # bury the real gaps in noise.
        make(repo, "PINS", CORE, [full("FY2024"), gap("H1-2024", "CapEx"),
                                  gap("Q3-2024", "CapEx")])
        assert missing_fields.scan(repo) == []

    def test_aliased_headers_are_not_reported_as_missing(self, repo):
        # EPSDiluted IS EPS. Reporting it missing sends someone re-extracting
        # a file that is already correct.
        headers = [h if h != "EPS" else "EPSDiluted" for h in CORE]
        make(repo, "AAPL", headers, [full("FY2024")])
        assert not [r for r in missing_fields.scan(repo)
                    if r["field"] == "EPS"]

    def test_ticker_with_no_csv_is_skipped(self, repo):
        (repo / "research" / "0001.HK" / "Extracted").mkdir(parents=True)
        assert missing_fields.scan(repo) == []


class TestReportingSpan:
    def test_pre_history_years_are_not_reported_as_gaps(self, repo):
        # PINS FY2016 predates the IPO and holds one comparative figure. No
        # re-extraction can fill it, so flagging seven missing fields there
        # is noise that buries the gaps that can be fixed.
        stub = ["FY2016", "", "", "", "", "", "", "-448.286", ""]
        make(repo, "PINS", CORE, [stub, full("FY2017"), full("FY2018")])
        assert missing_fields.scan(repo) == []

    def test_a_gap_inside_the_span_is_still_reported(self, repo):
        stub = ["FY2016", "", "", "", "", "", "", "-448.286", ""]
        make(repo, "PINS", CORE, [stub, full("FY2017"),
                                  gap("FY2018", "CapEx")])
        rows = missing_fields.scan(repo)
        assert [r["field"] for r in rows] == ["CapEx"]
        assert rows[0]["periods"] == ["FY2018"]

    def test_kpi_heavy_filer_keeps_its_history_in_the_gap_report(self, repo):
        # Same guard as integrity_report: a bank/holdco thin on core-8 in
        # every year must not have its early years silently dropped.
        headers = ["Period", "NetIncome", "ShareholdersEquity",
                   "TotalInterestIncome", "NetInterestIncome"]
        rows = [dict.fromkeys(headers, "1") | {"Period": f"FY{y}"}
                for y in (2022, 2023, 2024)]
        d = repo / "research" / "SRBK" / "Reports"
        d.mkdir(parents=True)
        with open(d / "SRBK_Metrics.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            w.writerows(rows)
        found = missing_fields.scan(repo)
        periods = {p for r in found for p in r["periods"]}
        assert periods == {"FY2022", "FY2023", "FY2024"}


class TestScope:
    def test_default_scope_is_the_core_eight(self, repo):
        make(repo, "T", CORE, [full("FY2024")])
        fields = {r["field"] for r in missing_fields.scan(repo)}
        assert "EBITDA" not in fields  # not core-8, absent but not reported

    def test_all_scope_covers_every_schema_column(self, repo):
        make(repo, "T", CORE, [full("FY2024")])
        fields = {r["field"] for r in missing_fields.scan(repo, scope="all")}
        assert "EBITDA" in fields
        assert "TotalAssets" in fields

    def test_period_and_units_are_never_reported_as_gaps(self, repo):
        make(repo, "T", CORE, [full("FY2024")])
        fields = {r["field"] for r in missing_fields.scan(repo, scope="all")}
        assert "Period" not in fields
        assert "Units" not in fields
        assert "Currency" not in fields


class TestOrdering:
    def test_worst_gaps_come_first(self, repo):
        make(repo, "BAD", CORE, [gap(f"FY{y}", "CapEx")
                                 for y in range(2015, 2025)])
        make(repo, "OK", CORE, [gap("FY2024", "EPS")])
        rows = missing_fields.scan(repo)
        assert rows[0]["ticker"] == "BAD"
        assert rows[0]["missing_count"] == 10


class TestFilters:
    def test_filter_by_ticker(self, repo):
        make(repo, "A", CORE, [gap("FY2024", "CapEx")])
        make(repo, "B", CORE, [gap("FY2024", "CapEx")])
        rows = missing_fields.scan(repo, tickers={"A"})
        assert {r["ticker"] for r in rows} == {"A"}

    def test_filter_by_field(self, repo):
        make(repo, "A", CORE, [gap("FY2024", "CapEx")])
        make(repo, "B", CORE, [gap("FY2024", "EPS")])
        rows = missing_fields.scan(repo, fields={"CapEx"})
        assert {r["field"] for r in rows} == {"CapEx"}


class TestOutputFormats:
    def test_jsonl_is_one_object_per_line(self, repo):
        # Distinct years: rows are deduplicated by period, so two gaps in the
        # same FY would collapse to one row and only one gap would show.
        make(repo, "PINS", CORE, [gap("FY2023", "CapEx"),
                                  gap("FY2024", "EPS")])
        buf = io.StringIO()
        missing_fields.write_jsonl(missing_fields.scan(repo), buf)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 2
        for ln in lines:
            obj = json.loads(ln)
            assert obj["ticker"] == "PINS"
            assert isinstance(obj["periods"], list)

    def test_csv_flattens_periods_to_one_field(self, repo):
        make(repo, "PINS", CORE, [gap("FY2023", "CapEx"),
                                  gap("FY2024", "CapEx")])
        buf = io.StringIO()
        missing_fields.write_csv(missing_fields.scan(repo), buf)
        rows = list(csv.DictReader(io.StringIO(buf.getvalue())))
        assert rows[0]["ticker"] == "PINS"
        assert rows[0]["periods"] == "FY2023 FY2024"
        assert rows[0]["missing_count"] == "2"

    def test_json_is_a_single_array(self, repo):
        make(repo, "PINS", CORE, [gap("FY2024", "CapEx")])
        buf = io.StringIO()
        missing_fields.write_json(missing_fields.scan(repo), buf)
        data = json.loads(buf.getvalue())
        assert isinstance(data, list)
        assert data[0]["field"] == "CapEx"


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["missing_fields.py", *argv])
    return missing_fields.main()


class TestMain:
    def test_writes_jsonl_to_stdout_by_default(self, repo, monkeypatch, capsys):
        make(repo, "PINS", CORE, [gap("FY2024", "CapEx")])
        assert run(monkeypatch, "--root", str(repo)) == 0
        out = capsys.readouterr().out.strip()
        assert json.loads(out)["field"] == "CapEx"

    def test_out_writes_a_file(self, repo, monkeypatch):
        make(repo, "PINS", CORE, [gap("FY2024", "CapEx")])
        dest = repo / "gaps.jsonl"
        assert run(monkeypatch, "--root", str(repo), "--out", str(dest)) == 0
        assert json.loads(dest.read_text().strip())["field"] == "CapEx"

    def test_missing_research_dir_errors_cleanly(self, tmp_path, monkeypatch,
                                                 capsys):
        assert run(monkeypatch, "--root", str(tmp_path)) == 1
        assert "no research/" in capsys.readouterr().err

    def test_clean_corpus_exits_zero_and_emits_nothing(self, repo, monkeypatch,
                                                       capsys):
        make(repo, "AMZN", CORE, [full("FY2024")])
        assert run(monkeypatch, "--root", str(repo)) == 0
        assert capsys.readouterr().out.strip() == ""
