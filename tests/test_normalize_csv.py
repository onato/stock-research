"""normalize_csv.py rewrites a Metrics CSV onto the canonical header.

62 distinct header shapes and 362 distinct column names accumulated across 80
CSVs -- the same metric spelled EPS, EPSDiluted, EPS_Diluted, Revenue_RMB_Mn.
export_csv.py has always written the canonical 24 columns, so the drift is in
files that predate it or were hand-written; this brings them into line.

Two rules the tests exist to hold:

* Nothing numeric may be lost. A column that does not map to a core metric is
  a company KPI (iPhoneRevenue, ARR, MAU) and is kept, appended after the core
  block -- the generated dashboards embed this CSV inline and chart those
  columns by name, so dropping or relocating them silently breaks charts.
* When two headers map to one core column (EPSBasic + EPSDiluted), diluted
  wins. It is the conservative per-share figure and the one a DCF should use.
"""

import csv

import normalize_csv
import pytest
import schema


def write(path, headers, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def read(path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


@pytest.fixture
def csv_path(tmp_path):
    return tmp_path / "T_Metrics.csv"


class TestHeaderMapping:
    def test_canonical_file_is_unchanged(self, csv_path):
        row = ["FY2024"] + ["1"] * (len(schema.CSV_HEADERS) - 1)
        write(csv_path, schema.CSV_HEADERS, [row])
        before = read(csv_path)
        assert normalize_csv.normalize_file(csv_path) is False  # no rewrite
        assert read(csv_path) == before

    def test_aliases_move_into_their_core_column(self, csv_path):
        write(csv_path, ["Period", "NetProfit", "Capex", "FCF"],
              [["FY2024", "10", "-3", "7"]])
        normalize_csv.normalize_file(csv_path)
        rows = read(csv_path)
        assert rows[0][:len(schema.CSV_HEADERS)] == schema.CSV_HEADERS
        got = dict(zip(rows[0], rows[1], strict=False))
        assert got["NetIncome"] == "10"
        assert got["CapEx"] == "-3"
        assert got["FreeCashFlow"] == "7"

    def test_unit_suffixed_headers_map_to_core(self, csv_path):
        # BABA/9988.HK bake the scale into the header.
        write(csv_path, ["Period", "Revenue_RMB_Mn", "NetIncome_RMB_Mn",
                         "EPS_Diluted_RMB"],
              [["FY2024", "941168", "79741", "3.91"]])
        normalize_csv.normalize_file(csv_path)
        got = dict(zip(*read(csv_path)[:2], strict=False))
        assert got["Revenue"] == "941168"
        assert got["NetIncome"] == "79741"
        assert got["EPS"] == "3.91"

    def test_every_canonical_column_is_present_even_when_absent_upstream(
            self, csv_path):
        write(csv_path, ["Period", "Revenue"], [["FY2024", "100"]])
        normalize_csv.normalize_file(csv_path)
        rows = read(csv_path)
        assert rows[0][:len(schema.CSV_HEADERS)] == schema.CSV_HEADERS
        got = dict(zip(rows[0], rows[1], strict=False))
        assert got["EBITDA"] == ""  # missing, not fabricated


class TestCollisions:
    def test_diluted_eps_wins_over_basic(self, csv_path):
        # AAPL, ASML, ADYEY and SFM all carry both. Diluted is the
        # conservative per-share figure and the one the DCF should use.
        write(csv_path, ["Period", "EPSBasic", "EPSDiluted"],
              [["FY2024", "0.65", "0.64"]])
        normalize_csv.normalize_file(csv_path)
        got = dict(zip(*read(csv_path)[:2], strict=False))
        assert got["EPS"] == "0.64"

    def test_the_losing_side_of_a_collision_is_kept_as_a_kpi(self, csv_path):
        # Basic EPS is real data; it must survive somewhere rather than being
        # silently dropped on the way to the canonical column.
        write(csv_path, ["Period", "EPSBasic", "EPSDiluted"],
              [["FY2024", "0.65", "0.64"]])
        normalize_csv.normalize_file(csv_path)
        rows = read(csv_path)
        assert "EPSBasic" in rows[0]
        got = dict(zip(rows[0], rows[1], strict=False))
        assert got["EPSBasic"] == "0.65"

    def test_a_losing_column_never_duplicates_a_canonical_header(self, csv_path):
        # If the loser keeps the name "Revenue" the output has two Revenue
        # columns, and every by-name reader (dashboards, DictReader, DuckDB
        # read_csv) silently takes the wrong one.
        write(csv_path, ["Period", "Revenue", "TotalRevenue"],
              [["FY2024", "", "500"]])
        normalize_csv.normalize_file(csv_path)
        header = read(csv_path)[0]
        assert header.count("Revenue") == 1
        assert "Revenue_alt" in header

    def test_a_populated_column_beats_an_empty_one(self, csv_path):
        write(csv_path, ["Period", "Revenue", "TotalRevenue"],
              [["FY2024", "", "500"]])
        normalize_csv.normalize_file(csv_path)
        got = dict(zip(*read(csv_path)[:2], strict=False))
        assert got["Revenue"] == "500"


class TestKpisSurvive:
    def test_company_kpis_are_kept_after_the_core_block(self, csv_path):
        # The generated dashboards embed this CSV inline and chart these by
        # name -- moving them to another file breaks the charts silently.
        write(csv_path, ["Period", "Revenue", "iPhoneRevenue", "ARR"],
              [["FY2024", "391035", "201183", "42"]])
        normalize_csv.normalize_file(csv_path)
        rows = read(csv_path)
        n = len(schema.CSV_HEADERS)
        assert rows[0][:n] == schema.CSV_HEADERS
        assert rows[0][n:] == ["iPhoneRevenue", "ARR"]
        got = dict(zip(rows[0], rows[1], strict=False))
        assert got["iPhoneRevenue"] == "201183"
        assert got["ARR"] == "42"

    def test_no_value_is_lost_anywhere(self, csv_path):
        headers = ["Period", "NetProfit", "iPhoneRevenue", "Notes"]
        write(csv_path, headers, [["FY2024", "10", "20", "a note"]])
        normalize_csv.normalize_file(csv_path)
        rows = read(csv_path)
        values = set(rows[1])
        for v in ("FY2024", "10", "20", "a note"):
            assert v in values


class TestRowsPreserved:
    def test_period_rows_and_order_are_preserved(self, csv_path):
        write(csv_path, ["Period", "Revenue"],
              [["FY2023", "1"], ["H1-2024", "2"], ["FY2024", "3"]])
        normalize_csv.normalize_file(csv_path)
        rows = read(csv_path)
        assert [r[0] for r in rows[1:]] == ["FY2023", "H1-2024", "FY2024"]

    def test_ragged_row_shorter_than_header_does_not_crash(self, csv_path):
        with open(csv_path, "w", newline="") as f:
            f.write("Period,Revenue,NetIncome\nFY2024,100\n")
        normalize_csv.normalize_file(csv_path)
        got = dict(zip(*read(csv_path)[:2], strict=False))
        assert got["Revenue"] == "100"
        assert got["NetIncome"] == ""

    def test_values_past_the_end_of_the_header_are_kept(self, csv_path):
        # 36 of META's rows carry 26 fields against a 25-column header, and
        # NVDA/PANW/FIG do the same. Indexing purely by header position drops
        # those orphan values silently -- 36 real numbers gone from META
        # alone. They get a synthetic column rather than being discarded.
        with open(csv_path, "w", newline="") as f:
            f.write("Period,Revenue\nFY2024,100,511\n")
        normalize_csv.normalize_file(csv_path)
        rows = read(csv_path)
        assert "511" in rows[1]
        assert "Extra1" in rows[0]

    def test_empty_file_is_left_alone(self, csv_path):
        csv_path.write_text("")
        assert normalize_csv.normalize_file(csv_path) is False
        assert csv_path.read_text() == ""


class TestIdempotent:
    def test_normalizing_twice_changes_nothing_the_second_time(self, csv_path):
        write(csv_path, ["Period", "NetProfit", "iPhoneRevenue"],
              [["FY2024", "10", "20"]])
        assert normalize_csv.normalize_file(csv_path) is True
        after = read(csv_path)
        assert normalize_csv.normalize_file(csv_path) is False
        assert read(csv_path) == after


class TestCheckMode:
    def test_check_reports_without_writing(self, csv_path):
        write(csv_path, ["Period", "NetProfit"], [["FY2024", "10"]])
        before = read(csv_path)
        plan = normalize_csv.plan_file(csv_path)
        assert plan["would_change"] is True
        assert ("NetProfit", "NetIncome") in plan["renames"]
        assert read(csv_path) == before  # untouched

    def test_check_flags_a_conforming_file_as_no_change(self, csv_path):
        row = ["FY2024"] + ["1"] * (len(schema.CSV_HEADERS) - 1)
        write(csv_path, schema.CSV_HEADERS, [row])
        assert normalize_csv.plan_file(csv_path)["would_change"] is False
