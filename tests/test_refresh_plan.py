"""refresh_plan.py decides how much work a stale ticker actually needs.

Re-running /research-stock on an existing ticker costs the same as a new one
(~32 min, ~$5-8): the skill has four "Always regenerate" directives, so all
four heavyweight subagents fire whether or not anything changed. Measured on
the corpus: of 20 tickers stale by valuation_date, 19 had no new filings at
all -- only 0285.HK did. The other 19 were stale in price and narrative only.

This module is the gate that tells those two cases apart, for free.
"""

import datetime as dt
import json

import periods
import pytest
import refresh_plan


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "research").mkdir()
    return tmp_path


def ticker_at(repo, ticker, *, filings=(), downloaded=(), csv_periods=(),
              days_old=0, price=100.0):
    """A researched ticker with given extracted filings and CSV rows.

    `downloaded` names filings that exist in PDFs/ but were never extracted --
    the state a mid-pipeline interruption leaves behind.
    """
    base = repo / "research" / ticker
    (base / "Extracted").mkdir(parents=True, exist_ok=True)
    (base / "Reports").mkdir(parents=True, exist_ok=True)
    for name in filings:
        (base / "Extracted" / name).write_text("x")
    if downloaded:
        (base / "PDFs").mkdir(parents=True, exist_ok=True)
    for name in downloaded:
        (base / "PDFs" / name).write_text("x")
    if csv_periods:
        rows = "\n".join(f"{p},1" for p in csv_periods)
        (base / "Reports" / f"{ticker}_Metrics.csv").write_text(
            "Period,Revenue\n" + rows)
    when = dt.date.today() - dt.timedelta(days=days_old)
    (base / "Reports" / f"{ticker}_DCF.json").write_text(json.dumps({
        "valuation_date": when.isoformat(),
        "current_price": price,
    }))
    (base / "Reports" / f"{ticker}_Dashboard.html").write_text("<html></html>")
    return base


class TestPeriodIdentity:
    """The gate compares (fiscal_year, sub_rank) -- never sort_key."""

    def test_period_identity_ignores_label_spelling(self):
        # The CSV writes `Q1-2026` while a filename carries `Q1 2026`. They
        # name the same quarter, so the gate must see them as equal.
        assert (refresh_plan.period_identity(periods.parse("Q1-2026"))
                == refresh_plan.period_identity(periods.parse("Q1 2026")))

    def test_sort_key_would_have_lied(self):
        # Why this module exists: sort_key's third element is the raw
        # uppercased label, so '-' (0x2D) sorts above ' ' (0x20) and
        # 'Q1-2026' > 'Q1 2026' on spelling alone. Using sort_key for the
        # gate reported 59 tickers with "new" data, 53 of them false.
        assert periods.sort_key("Q1-2026") > periods.sort_key("Q1 2026")

    def test_half_and_quarter_labels_agree_across_families(self):
        assert (refresh_plan.period_identity(periods.parse("H1 FY2026"))
                == refresh_plan.period_identity(periods.parse("H1-2026")))

    def test_full_year_outranks_its_own_quarters(self):
        fy = refresh_plan.period_identity(periods.parse("FY2026"))
        q4 = refresh_plan.period_identity(periods.parse("Q4-2026"))
        assert fy == (2026, 9)
        assert q4 == (2026, 4)
        assert fy > q4

    def test_later_year_outranks_earlier(self):
        assert (refresh_plan.period_identity(periods.parse("Q1-2026"))
                > refresh_plan.period_identity(periods.parse("FY2025")))

    def test_undated_period_has_no_identity(self):
        # A presentation labelled ASM-FY2024 must not become a phantom newest.
        assert refresh_plan.period_identity(periods.parse("ASM")) is None


class TestNewestExtracted:
    def test_newest_extracted_period_strips_part_suffix(self, repo):
        # 2CC.NZ_Annual_FY2021_Part1.txt: without stripping the _Part suffix
        # the period fails to parse and 271 corpus files drop out of the gate.
        ticker_at(repo, "2CC.NZ", filings=["2CC.NZ_Annual_FY2021_Part1.txt"])
        got = refresh_plan.newest_extracted_period(repo, "2CC.NZ")
        assert got is not None
        assert refresh_plan.period_identity(got) == (2021, 9)

    def test_newest_extracted_period_strips_dedup_suffix(self, repo):
        ticker_at(repo, "DCBO", filings=["DCBO_40F_FY2024_d2.txt"])
        got = refresh_plan.newest_extracted_period(repo, "DCBO")
        assert got is not None
        assert refresh_plan.period_identity(got) == (2024, 9)

    def test_newest_extracted_period_ignores_presentations(self, repo):
        ticker_at(repo, "AAA", filings=["AAA_Presentation_ASM.txt",
                                        "AAA_Annual_FY2024.txt"])
        got = refresh_plan.newest_extracted_period(repo, "AAA")
        assert refresh_plan.period_identity(got) == (2024, 9)

    def test_no_extracted_dir_yields_none(self, repo):
        (repo / "research" / "ZZZ").mkdir(parents=True)
        assert refresh_plan.newest_extracted_period(repo, "ZZZ") is None


class TestHasNewFilings:
    def test_has_new_filings_false_when_csv_covers_newest(self, repo):
        # The DCBO case: newest filing Q1-2026 is already the newest CSV row.
        ticker_at(repo, "DCBO",
                  filings=["DCBO_40F_FY2025.txt", "DCBO_6K_Q1-2026.txt"],
                  csv_periods=["FY2025", "Q1-2026"])
        assert refresh_plan.has_new_filings(repo, "DCBO") is False

    def test_has_new_filings_true_when_filing_newer(self, repo):
        # The 0285.HK case: a Q1 FY2026 filing the CSV stops short of.
        ticker_at(repo, "0285.HK",
                  filings=["0285.HK_Quarterly_Q1-2026.txt"],
                  csv_periods=["FY2025"])
        assert refresh_plan.has_new_filings(repo, "0285.HK") is True

    def test_spelling_difference_alone_is_not_new_data(self, repo):
        # The 53 false positives: filename says `Q1 2026`, CSV says `Q1-2026`.
        ticker_at(repo, "AAA", filings=["AAA_Quarterly_Q1 2026.txt"],
                  csv_periods=["Q1-2026"])
        assert refresh_plan.has_new_filings(repo, "AAA") is False

    def test_missing_csv_counts_as_new_data(self, repo):
        ticker_at(repo, "AAA", filings=["AAA_Annual_FY2025.txt"])
        assert refresh_plan.has_new_filings(repo, "AAA") is True


class TestPlanTier:
    def test_plan_tier_three_on_new_filings(self, repo):
        ticker_at(repo, "0285.HK", filings=["0285.HK_Quarterly_Q1-2026.txt"],
                  csv_periods=["FY2025"], days_old=48)
        plan = refresh_plan.plan_tier(repo, "0285.HK")
        assert plan.tier == 3

    def test_plan_tier_two_on_stale_no_new_data(self, repo):
        ticker_at(repo, "AAPL", filings=["AAPL_Quarterly_Q2-2026.txt"],
                  csv_periods=["Q2-2026"], days_old=61)
        plan = refresh_plan.plan_tier(repo, "AAPL")
        assert plan.tier == 2

    def test_plan_tier_zero_on_drift_only(self, repo):
        ticker_at(repo, "DCBO", filings=["DCBO_6K_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=10, price=17.52)
        plan = refresh_plan.plan_tier(repo, "DCBO", live_price=23.06)
        assert plan.tier == 0
        assert plan.drift_pct == pytest.approx(31.6, abs=0.1)

    def test_plan_tier_one_when_nothing_changed(self, repo):
        ticker_at(repo, "DCBO", filings=["DCBO_6K_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=10, price=17.52)
        plan = refresh_plan.plan_tier(repo, "DCBO", live_price=17.60)
        assert plan.tier == 1

    def test_new_filings_beat_drift(self, repo):
        # Real new data outranks a price move: the parser must run.
        ticker_at(repo, "AAA", filings=["AAA_Quarterly_Q1-2026.txt"],
                  csv_periods=["FY2025"], days_old=2, price=10.0)
        assert refresh_plan.plan_tier(repo, "AAA", live_price=99.0).tier == 3

    def test_plan_tier_three_when_csv_missing(self, repo):
        base = repo / "research" / "NEW" / "Reports"
        base.mkdir(parents=True)
        assert refresh_plan.plan_tier(repo, "NEW").tier == 3

    def test_plan_tier_three_when_dcf_missing(self, repo):
        base = repo / "research" / "NEW"
        (base / "Extracted").mkdir(parents=True)
        (base / "Reports").mkdir(parents=True)
        (base / "Reports" / "NEW_Metrics.csv").write_text("Period\nFY2025\n")
        assert refresh_plan.plan_tier(repo, "NEW").tier == 3

    def test_stale_beats_drift(self, repo):
        # A stale ticker needs its narrative refreshed even if price also moved;
        # tier 2 subsumes the free numeric pass.
        ticker_at(repo, "AAA", filings=["AAA_Quarterly_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=61, price=10.0)
        assert refresh_plan.plan_tier(repo, "AAA", live_price=99.0).tier == 2

    def test_unknown_live_price_never_reports_drift(self, repo):
        ticker_at(repo, "AAA", filings=["AAA_Quarterly_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=2, price=10.0)
        plan = refresh_plan.plan_tier(repo, "AAA", live_price=None)
        assert plan.tier == 1
        assert plan.drift_pct is None

    def test_plan_records_the_compared_periods(self, repo):
        ticker_at(repo, "DCBO", filings=["DCBO_6K_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=10)
        plan = refresh_plan.plan_tier(repo, "DCBO")
        assert plan.newest_filing == "Q1-2026"
        assert plan.newest_csv == "Q1-2026"
        assert plan.ticker == "DCBO"
        assert plan.reason


class TestDiedPartway:
    """A run that dies mid-pipeline leaves SOME deliverables behind.

    NZX.NZ (2026-08-24): the session limit hit during dashboard generation,
    after Metrics.csv and DCF.json were written. valuation_date was that
    morning, so the retry was classified tier 1 "nothing changed" and skipped
    the model -- the ticker stayed INCOMPLETE forever. A fresh DCF.json is
    not proof of a finished run; every deliverable must exist.
    """

    def test_missing_dashboard_is_tier_three(self, repo):
        base = ticker_at(repo, "NZX.NZ", filings=["NZX.NZ_Annual_FY2026.txt"],
                         csv_periods=["FY2026"], days_old=0)
        (base / "Reports" / "NZX.NZ_Dashboard.html").unlink()
        plan = refresh_plan.plan_tier(repo, "NZX.NZ")
        assert plan.tier == 3
        assert "Dashboard" in plan.reason

    def test_missing_csv_is_tier_three_even_with_fresh_dcf(self, repo):
        base = ticker_at(repo, "AAA", filings=["AAA_Presentation_ASM.txt"],
                         days_old=0)
        assert not (base / "Reports" / "AAA_Metrics.csv").exists()
        plan = refresh_plan.plan_tier(repo, "AAA")
        assert plan.tier == 3

    def test_complete_fresh_ticker_stays_tier_one(self, repo):
        ticker_at(repo, "AAA", filings=["AAA_Annual_FY2026.txt"],
                  csv_periods=["FY2026"], days_old=0)
        assert refresh_plan.plan_tier(repo, "AAA").tier == 1


class TestEdges:
    def test_undated_filing_only_is_not_new_data(self, repo):
        # Every filing is a presentation with no parseable period.
        ticker_at(repo, "AAA", filings=["AAA_Presentation_ASM.txt"],
                  csv_periods=["FY2025"])
        assert refresh_plan.has_new_filings(repo, "AAA") is False

    def test_unparseable_valuation_date_is_tier_three(self, repo):
        base = ticker_at(repo, "AAA", filings=["AAA_Annual_FY2025.txt"],
                         csv_periods=["FY2025"])
        (base / "Reports" / "AAA_DCF.json").write_text(
            json.dumps({"valuation_date": "not a date"}))
        assert refresh_plan.plan_tier(repo, "AAA").tier == 3

    def test_alternate_date_formats_parse(self, repo):
        base = ticker_at(repo, "AAA", filings=["AAA_Annual_FY2025.txt"],
                         csv_periods=["FY2025"])
        (base / "Reports" / "AAA_DCF.json").write_text(
            json.dumps({"valuation_date": "2026/08/01", "current_price": 5}))
        assert refresh_plan.plan_tier(repo, "AAA").age_days is not None

    def test_stored_price_absent_yields_none(self, repo):
        base = ticker_at(repo, "AAA", filings=["AAA_Annual_FY2025.txt"],
                         csv_periods=["FY2025"])
        (base / "Reports" / "AAA_DCF.json").write_text(
            json.dumps({"valuation_date": "2026-08-01"}))
        assert refresh_plan.stored_price(repo, "AAA") is None

    def test_stored_price_of_missing_dcf_is_none(self, repo):
        assert refresh_plan.stored_price(repo, "GHOST") is None


class TestCli:
    def _run(self, monkeypatch, capsys, repo, *argv):
        monkeypatch.setattr("sys.argv", ["refresh_plan.py", *argv])
        monkeypatch.setattr(refresh_plan, "REPO", repo)
        assert refresh_plan.main() == 0
        return capsys.readouterr().out

    def test_all_lists_every_ticker(self, repo, monkeypatch, capsys):
        ticker_at(repo, "DCBO", filings=["DCBO_6K_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=2)
        out = self._run(monkeypatch, capsys, repo, "--all")
        assert "DCBO" in out
        assert "1 tickers" in out

    def test_single_ticker(self, repo, monkeypatch, capsys):
        ticker_at(repo, "DCBO", filings=["DCBO_6K_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=60)
        out = self._run(monkeypatch, capsys, repo, "--ticker", "DCBO")
        assert "tier 2" in out

    def test_tier_filter(self, repo, monkeypatch, capsys):
        ticker_at(repo, "OLD", filings=["OLD_Quarterly_Q1-2026.txt"],
                  csv_periods=["FY2025"], days_old=60)
        ticker_at(repo, "FINE", filings=["FINE_Quarterly_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=2)
        out = self._run(monkeypatch, capsys, repo, "--all", "--tier", "3")
        assert "OLD" in out
        assert "FINE" not in out

    def test_no_research_dir(self, tmp_path, monkeypatch, capsys):
        out = self._run(monkeypatch, capsys, tmp_path, "--all")
        assert "0 tickers" in out


class TestUnextractedFilings:
    """A downloaded-but-unextracted filing is still new data.

    `has_new_filings` scanned only Extracted/*.txt, so a filing sitting in
    PDFs/ -- downloaded but never run through pdftotext -- was invisible to
    the gate. The ticker was reported tier 2 ("no new filings, narrative
    only"), which routes to /refresh-stock and explicitly skips the parser.
    The new period would then never be extracted, and the refresh would
    rebuild a valuation on a balance sheet a quarter out of date.

    Measured on DCBO: the Q2-2026 6-K sat in PDFs/ unextracted while the
    router reported tier 2. Net debt had moved from $15.9M to $52M.
    """

    def test_unextracted_filing_counts_as_new(self, repo):
        ticker_at(repo, "DCBO",
                  filings=["DCBO_6K_Q1-2026.txt"],
                  downloaded=["DCBO_6K_Q2-2026.htm"],
                  csv_periods=["Q1-2026"])
        assert refresh_plan.has_new_filings(repo, "DCBO") is True

    def test_unextracted_filing_routes_to_tier_3(self, repo):
        ticker_at(repo, "DCBO",
                  filings=["DCBO_6K_Q1-2026.txt"],
                  downloaded=["DCBO_6K_Q2-2026.htm"],
                  csv_periods=["Q1-2026"], days_old=53)
        assert refresh_plan.plan_tier(repo, "DCBO").tier == 3

    def test_already_extracted_filing_is_not_double_counted(self, repo):
        # The same period in both folders is not new -- the parser has run.
        ticker_at(repo, "DCBO",
                  filings=["DCBO_6K_Q2-2026.txt"],
                  downloaded=["DCBO_6K_Q2-2026.htm"],
                  csv_periods=["Q2-2026"], days_old=53)
        assert refresh_plan.has_new_filings(repo, "DCBO") is False

    def test_stale_download_older_than_csv_is_not_new(self, repo):
        # A leftover old PDF must not re-trigger the parser forever.
        ticker_at(repo, "DCBO",
                  filings=["DCBO_6K_Q2-2026.txt"],
                  downloaded=["DCBO_6K_Q1-2026.htm"],
                  csv_periods=["Q2-2026"], days_old=53)
        assert refresh_plan.has_new_filings(repo, "DCBO") is False

    def test_reason_names_the_unextracted_filing(self, repo):
        # The message must name the filing that actually triggered tier 3,
        # not the older extracted one -- otherwise the operator is told to
        # re-parse a period that is already in the CSV.
        ticker_at(repo, "DCBO",
                  filings=["DCBO_6K_Q1-2026.txt"],
                  downloaded=["DCBO_6K_Q2-2026.htm"],
                  csv_periods=["Q1-2026"], days_old=53)
        got = refresh_plan.plan_tier(repo, "DCBO")
        assert "Q2-2026" in got.reason
        assert got.newest_filing == "Q2-2026"


class TestTierTwoDoesNotClaimCurrentNumbers:
    """Tier 2's reason must not assert that no new filing exists.

    `has_new_filings` can only see the local disk. A filing that has been
    published but never downloaded is indistinguishable from no filing at
    all, so the ticker falls through to the age check and is reported as
    "stale (Nd) but no new filings: narrative only" -- routing it to a
    refresh that skips the parser BY DESIGN.

    Measured 2026-08-28: UBER, SFM and FISV were all reported tier 2 while
    their Q2-2026 10-Qs (filed 2026-08-05, 2026-07-29 and 2026-08-07) sat
    undownloaded and their CSVs stopped at Q1-2026. The same class of error
    as the unextracted-filing case above, one step earlier in the pipeline.

    The planner is offline by design, so it cannot know. It must therefore
    say what it actually checked rather than claiming a fact it cannot see.
    """

    def test_tier_two_reason_does_not_claim_no_new_filings(self, repo):
        ticker_at(repo, "UBER",
                  filings=["UBER_10Q_Q1-2026.txt"],
                  csv_periods=["Q1-2026"], days_old=59)
        got = refresh_plan.plan_tier(repo, "UBER")
        assert got.tier == 2
        assert "no new filings" not in got.reason
        assert "none downloaded" in got.reason
