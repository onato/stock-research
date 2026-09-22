"""fundamentals.scan reads the warehouse, not the per-ticker caches.

Until 2026-09-22 the screen opened every research/*/Reports/*.duckdb --
gitignored, rebuildable caches -- so a fresh checkout screened nothing and
a ticker whose cache had been deleted vanished from the screen without a
word. The warehouse is built from the committed files, so what the screen
sees is what is committed.
"""

import json
from pathlib import Path

import build_warehouse as bw
import dcf_engine
import fundamentals
import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    apa = root / "research" / "APA.AX" / "Reports"
    apa.mkdir(parents=True)
    (apa.parent / "info.json").write_text(json.dumps({"name": "APA Group", "sector": "Energy",
                                                        "fiscal_year_end": "06-30"}))
    drivers = json.loads((FIX / "dcf" / "APA_Drivers.json").read_text())
    (apa / "APA.AX_DCF.json").write_text(json.dumps(dcf_engine.build(drivers)))
    (apa / "APA.AX_Metrics.csv").write_text(
        "Period,Revenue,NetIncome,FreeCashFlow,ShareholdersEquity,TotalDebt,SharesOutstanding,Units,Currency\n"
        "FY2021,2605.3,0.7,901.9,2950.978,9924.038,1179.894,millions,AUD\n"
        "FY2022,2705.04,259.7,1080.6,2628.445,10904.32,1179.894,millions,AUD\n"
        "FY2023,2890,287,1070,1910,11523,1180,millions,AUD\n"
        "FY2024,3039,998,1073,3248,12922,1265,millions,AUD\n"
        "H1 FY2025,1621,34,552,2777,14285,1290,millions,AUD\n"
        "FY2025,3179,129,1083,2668,13977,1295,millions,AUD\n"
        "H1 FY2026,1614,95,556,2772,13170,1312,millions,AUD\n"
        "FY2026,2977,234,1118,2699,14087,1316,millions,AUD\n")
    # A thousands ticker with no DCF: scaled by the warehouse view, no price.
    k = root / "research" / "KMD.NZ" / "Reports"
    k.mkdir(parents=True)
    (k.parent / "info.json").write_text(json.dumps({"name": "KMD", "sector": "Retail"}))
    (k / "KMD.NZ_Metrics.csv").write_text(
        "Period,Revenue,NetIncome,Units,Currency\nFY2025,989000,30000,thousands,NZD\nFY2026,1000000,31000,thousands,NZD\n")
    # Researched but no Metrics CSV at all.
    (root / "research" / "AOF.NZ").mkdir()
    (root / "research" / "AOF.NZ" / "info.json").write_text("{}")
    (root / "state").mkdir()
    bw.build(root, root / "state" / "research.duckdb")
    return root


class TestScan:
    def test_reads_every_ticker_with_metrics_from_the_warehouse(self, repo):
        rows = {r.ticker: r for r in fundamentals.scan(repo)}
        assert set(rows) == {"APA.AX", "KMD.NZ"}
        apa = rows["APA.AX"]
        assert apa.currency == "AUD"
        assert apa.ttm_revenue == pytest.approx(2977.0)
        assert apa.ttm_basis in ("FY", "FY-TTM")   # depends on the real date; pinned below

        assert apa.debt_to_equity == pytest.approx(14087 / 2699, rel=1e-3)
        # Price and growth proxy come from the DCF document held in the warehouse.
        assert apa.peg is not None

    def test_fiscal_year_end_reaches_the_staleness_check(self, repo):
        import datetime as dt
        fresh = next(r for r in fundamentals.scan(repo, today=dt.date(2026, 9, 22)) if r.ticker == "APA.AX")
        stale = next(r for r in fundamentals.scan(repo, today=dt.date(2027, 6, 1)) if r.ticker == "APA.AX")
        assert fresh.ttm_basis == "FY-TTM"
        assert stale.ttm_basis == "FY"
        assert any(r.startswith("interim-overdue:H1 FY2027") for r in stale.reasons)
        # KMD.NZ has no fiscal_year_end in info.json: conservative.
        kmd = next(r for r in fundamentals.scan(repo, today=dt.date(2026, 9, 22)) if r.ticker == "KMD.NZ")
        assert kmd.ttm_basis == "FY"
        assert "fy-end-unknown" in kmd.reasons

    def test_thousands_are_scaled_to_millions(self, repo):
        kmd = next(r for r in fundamentals.scan(repo) if r.ticker == "KMD.NZ")
        assert kmd.ttm_revenue == pytest.approx(1000.0)
        assert "peg-no-dcf-growth" in kmd.reasons or kmd.peg is None

    def test_suffix_and_ticker_filters(self, repo):
        assert [r.ticker for r in fundamentals.scan(repo, suffix=".NZ")] == ["KMD.NZ"]
        assert [r.ticker for r in fundamentals.scan(repo, tickers={"APA.AX"})] == ["APA.AX"]

    def test_a_missing_warehouse_says_how_to_build_it(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="make warehouse"):
            fundamentals.scan(tmp_path)
