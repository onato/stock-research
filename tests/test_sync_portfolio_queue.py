"""queue/priority.txt is derived from the portfolio tracker, not hand-kept.

The file's own header promised `scripts/sync_portfolio_queue.py`, which never
existed, so the queue drifted: 6995.T was bought, DCBO and ADYEN.AS were added
to the watchlist, and none of them were queued. These tests pin the derivation
so the selector can read the tracker live and the committed file can be
regenerated for CI, where the sibling repo is absent.
"""

import json
import sys

import sync_portfolio_queue as spq


def write_portfolio(tmp_path, positions=(), watchlist=()):
    path = tmp_path / "user_portfolio.json"
    path.write_text(json.dumps({
        "cash_balances": {}, "dividends": [], "cash": {},
        "positions": list(positions), "watchlist": list(watchlist),
    }))
    return path


def held(ticker, *quantities):
    return {"ticker": ticker, "company_name": ticker,
            "lots": [{"quantity": q, "price": 1.0, "date": "2026-01-01",
                      "cost": q} for q in quantities]}


class TestPortfolioTickers:
    def test_held_positions_come_before_the_watchlist(self, tmp_path):
        p = write_portfolio(tmp_path, positions=[held("BABA", 10)],
                            watchlist=[{"ticker": "SFM"}])
        assert spq.portfolio_tickers(p) == [("BABA", "held"),
                                           ("SFM", "watchlist")]

    def test_a_fully_sold_position_is_not_held(self, tmp_path):
        # Lots net to zero once the position is closed; it must not keep a
        # queue slot ahead of the broad sweeps.
        p = write_portfolio(tmp_path, positions=[held("GONE", 100, -100),
                                                 held("BABA", 714, -364)])
        assert spq.portfolio_tickers(p) == [("BABA", "held")]

    def test_a_ticker_both_held_and_watched_is_listed_once_as_held(self,
                                                                   tmp_path):
        p = write_portfolio(tmp_path, positions=[held("BABA", 10)],
                            watchlist=[{"ticker": "BABA"}, {"ticker": "SFM"}])
        assert spq.portfolio_tickers(p) == [("BABA", "held"),
                                           ("SFM", "watchlist")]

    def test_order_within_each_group_follows_the_tracker(self, tmp_path):
        p = write_portfolio(tmp_path,
                            positions=[held("WISE.L", 1), held("6995.T", 1)],
                            watchlist=[{"ticker": "UBER"}, {"ticker": "DCBO"}])
        assert [t for t, _ in spq.portfolio_tickers(p)] == \
            ["WISE.L", "6995.T", "UBER", "DCBO"]

    def test_a_missing_tracker_returns_none_not_empty(self, tmp_path):
        # None = "no live source, use the committed file"; [] would mean
        # "the portfolio is empty" and silently drop every priority ticker.
        assert spq.portfolio_tickers(tmp_path / "nope.json") is None

    def test_a_corrupt_tracker_returns_none(self, tmp_path):
        p = tmp_path / "user_portfolio.json"
        p.write_text("{not json")
        assert spq.portfolio_tickers(p) is None

    def test_positions_without_lots_count_as_held(self, tmp_path):
        # Older entries carry no lot history; presence in `positions` is the
        # signal, absence of lots is not a sale.
        p = write_portfolio(tmp_path, positions=[{"ticker": "SEK.NZ"}])
        assert spq.portfolio_tickers(p) == [("SEK.NZ", "held")]


class TestRender:
    def test_renders_the_queue_file_with_tags(self):
        out = spq.render([("BABA", "held"), ("SFM", "watchlist")])
        lines = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
        assert lines == ["BABA       # held", "SFM        # watchlist"]

    def test_header_names_the_source_and_the_regeneration_command(self):
        out = spq.render([])
        assert "user_portfolio.json" in out
        assert "sync_portfolio_queue.py" in out

    def test_rendered_file_is_readable_by_the_selector(self, tmp_path):
        import select_ticker as st
        q = tmp_path / "priority.txt"
        q.write_text(spq.render([("BABA", "held"), ("SFM", "watchlist")]))
        assert st.read_tickers(q) == ["BABA", "SFM"]


class TestCli:
    def test_writes_the_queue_file(self, tmp_path, monkeypatch, capsys):
        p = write_portfolio(tmp_path, positions=[held("BABA", 1)],
                            watchlist=[{"ticker": "SFM"}])
        out = tmp_path / "queue" / "priority.txt"
        out.parent.mkdir()
        monkeypatch.setattr(sys, "argv", ["sync_portfolio_queue.py",
                                          "--portfolio", str(p),
                                          "--out", str(out)])
        assert spq.main() == 0
        text = out.read_text()
        assert "BABA" in text
        assert "SFM" in text

    def test_missing_tracker_leaves_the_file_alone_and_exits_zero(
            self, tmp_path, monkeypatch, capsys):
        # `make run` calls this unconditionally; on CI (no sibling repo) it
        # must be a no-op, not a failure, and must not blank the fallback.
        out = tmp_path / "priority.txt"
        out.write_text("KEEP\n")
        monkeypatch.setattr(sys, "argv", ["sync_portfolio_queue.py",
                                          "--portfolio",
                                          str(tmp_path / "nope.json"),
                                          "--out", str(out)])
        assert spq.main() == 0
        assert out.read_text() == "KEEP\n"
        assert "no portfolio" in capsys.readouterr().err.lower()
