"""Tests for exchange_eval.py: per-exchange extraction coverage.

scan_ticker() samples statutory filings (presentation decks would report a
parser failure where none exists), skips iXBRL machine markup, and counts
distinct metrics; main() aggregates per exchange and flags zero-yield
tickers. Tests build fake research/{T}/Extracted trees under tmp_path and
stub build_facts.scan_file — no live research/ reads, no real parser runs.
"""

import json
import sys

import exchange_eval
import pytest

PROSE = "Revenue for the year was $400.0 million.\n" * 5
# 4 distinctive taxonomy markers in the head trips the >3 threshold.
IXBRL = ("http://fasb.org/us-gaap/2024 xbrl.org ifrs-full\n"
         + "prose line\n" * 20)


@pytest.fixture
def fake_repo(monkeypatch, tmp_path):
    """Retarget exchange_eval.REPO at an empty tmp tree."""
    monkeypatch.setattr(exchange_eval, "REPO", tmp_path)
    return tmp_path


def extract(repo, ticker, *names, text=PROSE):
    d = repo / "research" / ticker / "Extracted"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text(text)
    return d


@pytest.fixture
def stub_scan(monkeypatch):
    """Canned build_facts.scan_file: records calls, yields per-ticker facts.

    Default yield is two core metrics; state["facts"][ticker] overrides
    (an empty list simulates a ticker the parser gets nothing from).
    """
    state = {"calls": [], "facts": {}}

    def fake(path):
        state["calls"].append(path)
        ticker = path.parent.parent.name
        default = [{"metric": "Revenue"}, {"metric": "NetIncome"}]
        return list(state["facts"].get(ticker, default))

    monkeypatch.setattr(exchange_eval.bf, "scan_file", fake)
    return state


class TestIsIxbrl:
    def test_four_markers_trip_the_threshold(self):
        assert exchange_eval.is_ixbrl(IXBRL) is True

    def test_threshold_is_strictly_more_than_three(self):
        assert exchange_eval.is_ixbrl("fasb.org us-gaap xbrl.org") is False

    def test_prose_is_not_ixbrl(self):
        assert exchange_eval.is_ixbrl(PROSE) is False

    def test_marker_matching_is_case_insensitive(self):
        """Extracts of uppercased taxonomy junk are still machine markup."""
        assert exchange_eval.is_ixbrl(IXBRL.upper()) is True

    def test_prose_mentions_of_xbrl_are_not_markup(self):
        """A filing that *discusses* XBRL is prose, not taxonomy junk --
        the bare word must not count as a marker."""
        prose = ("The company files its accounts in XBRL format. "
                 "XBRL adoption is mandatory. xbrl xbrl xbrl xbrl")
        assert exchange_eval.is_ixbrl(prose) is False

    def test_markers_past_a_prose_preamble_are_still_seen(self):
        """Real iXBRL extracts open with a prose cover page; markers a few
        thousand chars in must still be inside the scan window."""
        assert exchange_eval.is_ixbrl("prose " * 1500 + IXBRL) is True

    def test_scan_window_is_bounded_at_20000_chars(self):
        assert exchange_eval.is_ixbrl("x" * 20000 + IXBRL * 5) is False


class TestExchangeOf:
    def test_known_suffixes(self):
        assert exchange_eval.exchange_of("SEK.NZ") == ("NZX", "PDF only")
        assert exchange_eval.exchange_of("FIH.U")[0] == "US (unit)"
        assert exchange_eval.exchange_of("WISE.L")[0] == "LSE"

    def test_no_suffix_is_us(self):
        assert exchange_eval.exchange_of("DUOL") == (
            "US", "SEC XBRL (companyfacts)")

    def test_suffix_matching_is_case_insensitive(self):
        assert exchange_eval.exchange_of("sek.nz")[0] == "NZX"

    def test_unknown_suffix_falls_back_to_itself(self):
        assert exchange_eval.exchange_of("FOO.XX") == ("XX", "unknown")


class TestScanTicker:
    def test_missing_or_empty_extracted_returns_none(self, fake_repo, stub_scan):
        assert exchange_eval.scan_ticker("GHOST.NZ") is None
        (fake_repo / "research" / "EMPTY.NZ" / "Extracted").mkdir(parents=True)
        assert exchange_eval.scan_ticker("EMPTY.NZ") is None

    def test_statutory_filings_preferred_over_decks(self, fake_repo, stub_scan):
        """Presentations are stylised slide text with no statement tables;
        sampling them reports a parser failure where none exists."""
        extract(fake_repo, "T.NZ", "T.NZ_Annual_FY2024.txt",
                "T.NZ_Presentation_FY2024.txt", "T.NZ_Presentation_H1-2024.txt")
        st = exchange_eval.scan_ticker("T.NZ")
        assert st["filings"] == 3
        assert st["sampled"] == 1
        assert [p.name for p in stub_scan["calls"]] == ["T.NZ_Annual_FY2024.txt"]

    def test_falls_back_to_decks_when_nothing_statutory(self, fake_repo, stub_scan):
        extract(fake_repo, "T.NZ", "T.NZ_Presentation_FY2024.txt")
        st = exchange_eval.scan_ticker("T.NZ")
        assert st["sampled"] == 1
        assert st["facts"] == 2

    def test_samples_both_ends_of_the_pool(self, fake_repo, stub_scan):
        """Newest filings matter most, oldest exercise layout variation."""
        names = [f"T.NZ_Annual_FY{y}.txt" for y in range(2017, 2025)]
        extract(fake_repo, "T.NZ", *names)
        st = exchange_eval.scan_ticker("T.NZ", sample=6)
        assert st["filings"] == 8
        assert st["sampled"] == 6
        picked = sorted(p.name for p in stub_scan["calls"])
        assert picked == [f"T.NZ_Annual_FY{y}.txt"
                          for y in (2017, 2018, 2019, 2022, 2023, 2024)]

    def test_small_pools_are_deduplicated_not_double_counted(self, fake_repo,
                                                             stub_scan):
        names = [f"T.NZ_Annual_FY{y}.txt" for y in (2023, 2024)]
        extract(fake_repo, "T.NZ", *names)
        st = exchange_eval.scan_ticker("T.NZ", sample=6)
        assert st["sampled"] == 2
        assert len(stub_scan["calls"]) == 2

    def test_ixbrl_filings_counted_and_never_scanned(self, fake_repo, stub_scan):
        extract(fake_repo, "T.NZ", "T.NZ_Annual_FY2024.txt", text=IXBRL)
        st = exchange_eval.scan_ticker("T.NZ")
        assert st["ixbrl"] == 1
        assert st["facts"] == 0
        assert stub_scan["calls"] == []

    def test_metrics_are_distinct_and_sorted(self, fake_repo, stub_scan):
        stub_scan["facts"]["T.NZ"] = [
            {"metric": "Revenue"}, {"metric": "Revenue"}, {"metric": "EPS"}]
        extract(fake_repo, "T.NZ", "T.NZ_Annual_FY2024.txt")
        st = exchange_eval.scan_ticker("T.NZ")
        assert st["facts"] == 3
        assert st["metrics_found"] == 2
        assert st["metrics"] == ["EPS", "Revenue"]


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["exchange_eval.py", *argv])
    return exchange_eval.main()


class TestMain:
    def test_empty_repo_returns_1(self, fake_repo, stub_scan, monkeypatch,
                                  capsys):
        assert run_main(monkeypatch) == 1
        assert "no extracted filings found" in capsys.readouterr().err

    def test_zero_yield_tickers_are_flagged(self, fake_repo, stub_scan,
                                            monkeypatch, capsys):
        """A ticker yielding NO facts is the failure that matters."""
        extract(fake_repo, "AAA.NZ", "AAA.NZ_Annual_FY2024.txt")
        extract(fake_repo, "BBB.NZ", "BBB.NZ_Annual_FY2024.txt")
        stub_scan["facts"]["BBB.NZ"] = []
        assert run_main(monkeypatch) == 0
        out = capsys.readouterr().out
        assert "NZX" in out
        assert "1 ticker(s) yield NOTHING" in out

    def test_ixbrl_only_tickers_are_not_counted_as_zero(self, fake_repo,
                                                        stub_scan, monkeypatch,
                                                        capsys):
        extract(fake_repo, "AAA.NZ", "AAA.NZ_Annual_FY2024.txt", text=IXBRL)
        assert run_main(monkeypatch) == 0
        assert "yield NOTHING" not in capsys.readouterr().out

    def test_exchange_filter_matches_label_or_suffix(self, fake_repo, stub_scan,
                                                     monkeypatch, capsys):
        extract(fake_repo, "AAA.NZ", "AAA.NZ_Annual_FY2024.txt")
        extract(fake_repo, "DUOL", "DUOL_10K_FY2024.txt")
        assert run_main(monkeypatch, "--exchange", "NZ") == 0
        out = capsys.readouterr().out
        assert "NZX" in out
        assert "SEC XBRL" not in out
        # The label works too, case-insensitively.
        assert run_main(monkeypatch, "--exchange", "nzx") == 0
        assert "NZX" in capsys.readouterr().out

    def test_unit_group_selectable_by_label_or_suffix_key(self, fake_repo,
                                                          stub_scan,
                                                          monkeypatch, capsys):
        """The .U group answers to its exact display label and its suffix
        key -- and the suffix branch matches the group's suffix key, never
        the raw ticker tail (a suffixless ticker's tail is its whole name,
        which is not an exchange)."""
        extract(fake_repo, "FIH.U", "FIH.U_Annual_FY2024.txt")
        extract(fake_repo, "DUOL", "DUOL_10K_FY2024.txt")
        assert run_main(monkeypatch, "--exchange", "US (unit)") == 0
        out = capsys.readouterr().out
        assert "US (unit)" in out
        assert "companyfacts" not in out    # plain-US group excluded
        assert run_main(monkeypatch, "--exchange", "U") == 0
        assert "US (unit)" in capsys.readouterr().out
        # A bare ticker name selects nothing.
        assert run_main(monkeypatch, "--exchange", "DUOL") == 1
        assert "no extracted filings" in capsys.readouterr().err

    def test_json_snapshot(self, fake_repo, stub_scan, monkeypatch, tmp_path,
                           capsys):
        extract(fake_repo, "AAA.NZ", "AAA.NZ_Annual_FY2024.txt")
        fp = tmp_path / "out.json"
        assert run_main(monkeypatch, "--json", str(fp)) == 0
        snap = json.loads(fp.read_text())
        assert snap["NZX"]["tickers"] == 1
        assert snap["NZX"]["zero"] == 0
        assert snap["NZX"]["regime"] == "PDF only"
        # 2 of the 25 core PATTERNS metrics seen -> 8.0%
        assert snap["NZX"]["metric_coverage_pct"] == pytest.approx(
            2 / len(exchange_eval.bf.PATTERNS) * 100, abs=0.05)

    def test_never_yielded_metrics_reported_as_pattern_gaps(self, fake_repo,
                                                            stub_scan,
                                                            monkeypatch,
                                                            capsys):
        extract(fake_repo, "AAA.NZ", "AAA.NZ_Annual_FY2024.txt")
        assert run_main(monkeypatch) == 0
        out = capsys.readouterr().out
        n_never = len(exchange_eval.bf.PATTERNS) - 2   # Revenue, NetIncome seen
        assert f"Metrics NO ticker ever yields ({n_never})" in out
        assert "pattern gaps" in out

    def test_verbose_lists_per_ticker(self, fake_repo, stub_scan, monkeypatch,
                                      capsys):
        extract(fake_repo, "AAA.NZ", "AAA.NZ_Annual_FY2024.txt")
        stub_scan["facts"]["AAA.NZ"] = []
        assert run_main(monkeypatch, "--verbose") == 0
        out = capsys.readouterr().out
        assert "AAA.NZ" in out
        assert "ZERO" in out
