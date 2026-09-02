"""Slowly fill in the company name and business description we don't have.

877 of the 1,784 queued tickers are a bare symbol: no name, no sector, no
summary. `screen_ethics.py` marks them `status: unchecked`, and an unchecked
ticker is the one that can waste a whole research run on an obvious ethical
mismatch.

This backfill is deliberately slow. Yahoo rate-limited this machine into a
multi-hour 429 cooldown during development, so the script is built to run for
a week at a crawl rather than to finish quickly: a delay between every
request, a long backoff when a 429 does arrive, a nightly cap, and resumable
state so it picks up where it stopped. Nothing here is optimised for speed --
finishing in a week without getting blocked is the whole design goal.

It writes only `name`, `sector` and `business_summary` into info.json, and
never touches a field a human or the research pipeline owns.
"""

import json

import backfill_profiles as bp
import pytest


class TestProfileUrl:
    """stockanalysis.com paths differ per market; a wrong path is a 404."""

    @pytest.mark.parametrize(("ticker", "want"), [
        ("AAPL", "https://stockanalysis.com/stocks/AAPL/company/"),
        ("0857.HK", "https://stockanalysis.com/quote/hkg/0857/company/"),
        ("2914.T", "https://stockanalysis.com/quote/tyo/2914/company/"),
        ("D05.SI", "https://stockanalysis.com/quote/sgx/D05/company/"),
        ("BHP.AX", "https://stockanalysis.com/quote/asx/BHP/company/"),
        ("SPK.NZ", "https://stockanalysis.com/quote/nzx/SPK/company/"),
        ("BATS.L", "https://stockanalysis.com/quote/lon/BATS/company/"),
        ("RY.TO", "https://stockanalysis.com/quote/tsx/RY/company/"),
        ("SAN.PA", "https://stockanalysis.com/quote/epa/SAN/company/"),
        ("SAP.DE", "https://stockanalysis.com/quote/etr/SAP/company/"),
        ("INFY.NS", "https://stockanalysis.com/quote/nse/INFY/company/"),
    ])
    def test_maps_each_market(self, ticker, want):
        assert bp.profile_url(ticker) == want

    def test_unknown_suffix_has_no_url(self):
        """Better to skip than to guess a path and bank a 404 as 'no data'."""
        assert bp.profile_url("FOO.XYZ") is None


class TestParseProfile:
    """Pull the name and the description out, and none of the site chrome."""

    # Trimmed from the live stockanalysis.com markup: the h1 is the literal
    # "Company Description", the name is only in <title>, and the paragraphs
    # sit in a following div with no section heading of their own.
    PAGE = """
    <html><head><title>Japan Tobacco (TYO:2914) Company Profile &amp; Description</title></head>
    <body>
      <nav><p>Help Log In Sign Up Home Watchlist Stocks Stock Screener</p></nav>
      <h1 class="mb-3 text-2xl font-bold">Company Description</h1>
      <div class="mb-5 text-base"><!----><p>Japan Tobacco Inc., a tobacco company,
         engages in the manufacture and sale of tobacco products in Japan and
         internationally. It operates through two segments: Tobacco Business and
         Processed Food Business.</p><p>It offers tobacco products, such as
         cigarettes, fine cut tobacco products, cigars, pipes, and hookah
         products.</p></div>
      <h2>Contact Details</h2>
      <p>2-1, Toranomon 4-chome, Minato-ku, Tokyo, Japan 105-6927 is the address</p>
    </body></html>
    """

    def test_extracts_the_description(self):
        got = bp.parse_profile(self.PAGE)
        assert "tobacco company" in got["business_summary"]
        assert "cigarettes" in got["business_summary"]

    def test_takes_the_name_from_the_title_not_the_h1(self):
        """The live h1 is the literal string "Company Description"."""
        got = bp.parse_profile(self.PAGE)
        assert got["name"] == "Japan Tobacco"

    def test_drops_navigation_chrome(self):
        got = bp.parse_profile(self.PAGE)
        assert "Log In" not in got["business_summary"]
        assert "Stock Screener" not in got["business_summary"]

    def test_stops_before_the_next_section(self):
        """The postal address is not part of what the company does."""
        got = bp.parse_profile(self.PAGE)
        assert "Toranomon" not in got["business_summary"]

    def test_empty_page_yields_nothing_rather_than_junk(self):
        assert bp.parse_profile("<html><body></body></html>") == {}


class TestRateLimiting:
    """The pacing IS the feature; a fast run gets this machine blocked."""

    def test_there_is_a_delay_between_requests(self):
        assert bp.DELAY_SECONDS >= 3, "too fast; Yahoo 429'd at a lower rate"

    def test_a_429_backs_off_far_longer_than_a_normal_delay(self):
        assert bp.backoff_seconds(1) >= 300
        assert bp.backoff_seconds(2) > bp.backoff_seconds(1), "must escalate"
        assert bp.backoff_seconds(9) <= bp.MAX_BACKOFF, "must stay bounded"

    def test_a_run_is_capped_so_a_week_is_actually_a_week(self):
        assert 0 < bp.DEFAULT_LIMIT <= 400


class TestState:
    """Resumable: a week-long job is interrupted many times."""

    def test_records_done_and_failed_separately(self, tmp_path):
        p = tmp_path / "state.json"
        bp.save_state(p, {"done": {"AAPL": "2026-09-02"},
                          "failed": {"FOO.XYZ": "no-url"}})
        s = bp.load_state(p)
        assert "AAPL" in s["done"]
        assert s["failed"]["FOO.XYZ"] == "no-url"

    def test_missing_state_file_starts_clean(self, tmp_path):
        s = bp.load_state(tmp_path / "nope.json")
        assert s == {"done": {}, "failed": {}}

    def test_already_done_tickers_are_not_refetched(self, tmp_path):
        p = tmp_path / "state.json"
        bp.save_state(p, {"done": {"AAPL": "2026-09-02"}, "failed": {}})
        todo = bp.pending(["AAPL", "MSFT"], bp.load_state(p))
        assert todo == ["MSFT"]

    def test_a_permanent_failure_is_not_retried_forever(self, tmp_path):
        """A delisted symbol 404s every time; retrying it wastes the budget."""
        p = tmp_path / "state.json"
        bp.save_state(p, {"done": {}, "failed": {"GONE.L": "http-404"}})
        assert bp.pending(["GONE.L", "MSFT"], bp.load_state(p)) == ["MSFT"]

    def test_a_transient_failure_is_retried(self, tmp_path):
        """A 429 or timeout says nothing about the ticker; try it again."""
        p = tmp_path / "state.json"
        bp.save_state(p, {"done": {}, "failed": {"MSFT": "http-429"}})
        assert "MSFT" in bp.pending(["MSFT"], bp.load_state(p))


class TestWriteBack:
    """Fill only the gaps; never overwrite what is already known."""

    def test_fills_an_empty_info(self, tmp_path):
        p = tmp_path / "info.json"
        p.write_text(json.dumps({"name": ""}))
        bp.merge_info(p, {"name": "Japan Tobacco Inc.",
                          "business_summary": "a tobacco company"})
        d = json.loads(p.read_text())
        assert d["name"] == "Japan Tobacco Inc."
        assert d["business_summary"] == "a tobacco company"

    def test_never_overwrites_a_curated_name(self, tmp_path):
        """A human or the research pipeline may have written a better one."""
        p = tmp_path / "info.json"
        p.write_text(json.dumps({"name": "RTO Limited (formerly Blackwell)",
                                 "quirks": "renamed 2024"}))
        bp.merge_info(p, {"name": "RTO Ltd", "business_summary": "x"})
        d = json.loads(p.read_text())
        assert d["name"] == "RTO Limited (formerly Blackwell)"
        assert d["quirks"] == "renamed 2024"
        assert d["business_summary"] == "x", "the missing field still fills"

    def test_records_where_the_text_came_from(self, tmp_path):
        p = tmp_path / "info.json"
        p.write_text(json.dumps({}))
        bp.merge_info(p, {"name": "X", "business_summary": "y"})
        d = json.loads(p.read_text())
        assert d["profile_source"] == "stockanalysis.com"
        assert d["profile_fetched_at"]


class TestCommit:
    """A week-long unattended run must leave its work committed.

    Run nightly from `make run`, the backfill writes hundreds of info.json
    files. Left uncommitted they pile up in the working tree, get swept into
    an unrelated ticker's commit by `git add -A`, or are lost to a checkout --
    the same class of problem as the uncommitted price refreshes.
    """

    @staticmethod
    def _fake_git(calls, msgs=None):
        """Stub matching git's exit codes.

        `diff --cached --quiet` returns 1 when something IS staged -- the
        inverse of the usual convention, and easy to get backwards.
        """
        def run(*a, **k):
            calls.append(a)
            if a[:3] == ("diff", "--cached", "--quiet"):
                return 1, ""          # 1 = there are staged changes
            if msgs is not None and a and a[0] == "commit":
                msgs.append(" ".join(a))
            return 0, ""
        return run

    def test_commits_only_the_files_it_wrote(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(bp, "_git", self._fake_git(calls))
        bp.commit_profiles(["research/AAPL/info.json", "research/MSFT/info.json"],
                           filled=2, root=tmp_path)
        added = [a for a in calls if a and a[0] == "add"]
        assert added, "nothing staged"
        staged = set(added[0][1:])
        assert "research/AAPL/info.json" in staged
        assert "state/profile_backfill.json" in staged, "resume state must travel"
        assert not any("-A" in a for a in added), (
            "git add -A would sweep in unrelated work from a concurrent run"
        )

    def test_says_what_it_did_in_the_message(self, tmp_path, monkeypatch):
        msgs: list[str] = []
        monkeypatch.setattr(bp, "_git", self._fake_git([], msgs))
        bp.commit_profiles(["research/AAPL/info.json"], filled=1, root=tmp_path)
        assert msgs, "no commit issued"
        assert "1" in msgs[0]
        assert "chore:" in msgs[0], "project uses Conventional Commits"

    def test_nothing_written_means_no_commit(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(bp, "_git", self._fake_git(calls))
        bp.commit_profiles([], filled=0, root=tmp_path)
        assert not any(a and a[0] == "commit" for a in calls), (
            "an empty run must not create an empty commit"
        )
