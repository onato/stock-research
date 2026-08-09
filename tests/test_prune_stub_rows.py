"""prune_stub_rows.py deletes pre-history rows from the metrics CSVs.

A row like PINS FY2016 -- one equity figure carried in from a 10-K
comparative, for a year the company was private -- is not a data point. It
cannot be filled by re-extraction, and leaving it in makes every report count
seven phantom gaps against the ticker.

The rows to delete come from integrity_report.reporting_span(), so this tool
and the reports agree by construction. What is added here is the safety rule:
a row is only deleted when it carries nothing worth keeping, so that widening
the span heuristic later can never silently eat real data.
"""

import csv

import prune_stub_rows
import pytest


def write(path, rows, headers=None):
    headers = headers or ["Period", "Revenue", "NetIncome", "EPS",
                          "OperatingCashFlow", "CapEx", "FreeCashFlow",
                          "ShareholdersEquity", "SharesOutstanding"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def full(period):
    return {"Period": period, "Revenue": "1", "NetIncome": "1", "EPS": "1",
            "OperatingCashFlow": "1", "CapEx": "1", "FreeCashFlow": "1",
            "ShareholdersEquity": "1", "SharesOutstanding": "1"}


@pytest.fixture
def csv_path(tmp_path):
    d = tmp_path / "research" / "PINS" / "Reports"
    d.mkdir(parents=True)
    return d / "PINS_Metrics.csv"


class TestPruning:
    def test_a_pre_history_stub_row_is_removed(self, csv_path):
        write(csv_path, [{"Period": "FY2016", "ShareholdersEquity": "-448.3"},
                         full("FY2017"), full("FY2018")])
        assert prune_stub_rows.prune_file(csv_path) == ["FY2016"]
        assert [r["Period"] for r in read(csv_path)] == ["FY2017", "FY2018"]

    def test_several_leading_stubs_are_removed(self, csv_path):
        rows = [{"Period": f"FY{y}"} for y in (2015, 2016, 2017, 2018)]
        write(csv_path, [*rows, full("FY2019"), full("FY2020")])
        assert prune_stub_rows.prune_file(csv_path) == [
            "FY2015", "FY2016", "FY2017", "FY2018"]
        assert [r["Period"] for r in read(csv_path)] == ["FY2019", "FY2020"]

    def test_a_clean_file_is_untouched(self, csv_path):
        write(csv_path, [full("FY2023"), full("FY2024")])
        before = csv_path.read_text()
        assert prune_stub_rows.prune_file(csv_path) == []
        assert csv_path.read_text() == before

    def test_non_fy_rows_are_preserved(self, csv_path):
        # Quarterly rows for an in-scope year must survive; only the whole
        # pre-history FY rows go.
        write(csv_path, [{"Period": "FY2016", "ShareholdersEquity": "-1"},
                         full("FY2017"), full("Q1 2017")])
        prune_stub_rows.prune_file(csv_path)
        assert [r["Period"] for r in read(csv_path)] == ["FY2017", "Q1 2017"]

    def test_a_quarterly_row_in_a_pruned_year_is_also_removed(self, csv_path):
        # If FY2016 predates the company's reporting, so does Q1 2016.
        write(csv_path, [{"Period": "FY2016", "ShareholdersEquity": "-1"},
                         {"Period": "Q1 2016", "ShareholdersEquity": "-1"},
                         full("FY2017"), full("FY2018")])
        prune_stub_rows.prune_file(csv_path)
        assert [r["Period"] for r in read(csv_path)] == ["FY2017", "FY2018"]

    def test_header_and_column_order_survive(self, csv_path):
        write(csv_path, [{"Period": "FY2016", "ShareholdersEquity": "-1"},
                         full("FY2017")])
        before = csv_path.read_text().splitlines()[0]
        prune_stub_rows.prune_file(csv_path)
        assert csv_path.read_text().splitlines()[0] == before


class TestSafety:
    def test_a_row_with_revenue_is_never_deleted(self, csv_path):
        # The span heuristic should not select such a row, but if it ever
        # does, a year carrying real income-statement data must survive.
        write(csv_path, [{"Period": "FY2016", "Revenue": "100"},
                         full("FY2017"), full("FY2018"), full("FY2019")])
        assert prune_stub_rows.prune_file(csv_path) == []
        assert len(read(csv_path)) == 4

    def test_equity_only_rows_are_deletable(self, csv_path):
        # ShareholdersEquity alone is the comparative-carry signature and is
        # the one core field that does not block deletion.
        write(csv_path, [{"Period": "FY2016", "ShareholdersEquity": "-1",
                          "SharesOutstanding": ""},
                         full("FY2017"), full("FY2018")])
        assert prune_stub_rows.prune_file(csv_path) == ["FY2016"]

    def test_units_and_currency_labels_do_not_protect_a_row(self, csv_path):
        # Every row carries Units/Currency; they label the other numbers
        # rather than being data, so a row holding nothing else is still
        # empty. Treating them as content blocked every real deletion.
        headers = ["Period", "Revenue", "NetIncome", "EPS",
                   "OperatingCashFlow", "CapEx", "FreeCashFlow",
                   "ShareholdersEquity", "SharesOutstanding",
                   "Units", "Currency"]
        write(csv_path, [{"Period": "FY2016", "ShareholdersEquity": "-1",
                          "Units": "millions", "Currency": "USD"},
                         {**full("FY2017"), "Units": "millions",
                          "Currency": "USD"},
                         {**full("FY2018"), "Units": "millions",
                          "Currency": "USD"}],
              headers=headers)
        assert prune_stub_rows.prune_file(csv_path) == ["FY2016"]

    def test_a_row_with_a_kpi_value_is_kept(self, csv_path):
        # A company KPI is still data; only genuinely empty years go.
        headers = ["Period", "Revenue", "NetIncome", "EPS",
                   "OperatingCashFlow", "CapEx", "FreeCashFlow",
                   "ShareholdersEquity", "SharesOutstanding", "ARR"]
        write(csv_path, [{"Period": "FY2016", "ARR": "42"},
                         full("FY2017"), full("FY2018"), full("FY2019")],
              headers=headers)
        assert prune_stub_rows.prune_file(csv_path) == []


class TestPlan:
    def test_check_mode_reports_without_writing(self, csv_path):
        write(csv_path, [{"Period": "FY2016", "ShareholdersEquity": "-1"},
                         full("FY2017"), full("FY2018")])
        before = csv_path.read_text()
        plan = prune_stub_rows.plan_file(csv_path)
        assert plan["periods"] == ["FY2016"]
        assert plan["values"] == [{"ShareholdersEquity": "-1"}]
        assert csv_path.read_text() == before

    def test_plan_is_empty_for_a_clean_file(self, csv_path):
        write(csv_path, [full("FY2023"), full("FY2024")])
        assert prune_stub_rows.plan_file(csv_path)["periods"] == []


class TestIdempotent:
    def test_pruning_twice_is_a_no_op_the_second_time(self, csv_path):
        write(csv_path, [{"Period": "FY2016", "ShareholdersEquity": "-1"},
                         full("FY2017"), full("FY2018")])
        assert prune_stub_rows.prune_file(csv_path) == ["FY2016"]
        after = csv_path.read_text()
        assert prune_stub_rows.prune_file(csv_path) == []
        assert csv_path.read_text() == after
