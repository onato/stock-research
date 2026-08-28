"""extract.py routes a ticker to the right extraction path.

US bare symbols try SEC XBRL first and fall back to text extraction when
XBRL yields nothing; suffixed tickers go straight to text. Every dead end
is logged as a gap so coverage problems accumulate somewhere visible
instead of vanishing — and main() always exits 0, because a ticker the
pipeline cannot extract is handed to the agent, not treated as a failure.
"""

import sys
from types import SimpleNamespace

import duckdb
import extract
import schema


class TestIsUsSymbol:
    def test_bare_symbol_is_us(self):
        assert extract.is_us_symbol("NFLX")

    def test_suffixed_symbols_are_not(self):
        # A suffixed ticker is never in SEC's map, so the lookup is skipped.
        assert not extract.is_us_symbol("AGL.NZ")
        assert not extract.is_us_symbol("WISE.L")
        assert not extract.is_us_symbol("0285.HK")


class TestRun:
    def test_invokes_script_from_scripts_dir(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=3, stdout="out", stderr="err")

        monkeypatch.setattr(extract.subprocess, "run", fake_run)
        rc, out = extract.run("build_facts.py", "NFLX", "--show")
        assert rc == 3
        assert out == "outerr"  # stdout and stderr concatenated, in that order
        assert captured["cmd"][0] == sys.executable
        assert captured["cmd"][1] == str(extract.SCRIPTS / "build_facts.py")
        assert captured["cmd"][2:] == ["NFLX", "--show"]
        assert captured["kwargs"]["check"] is False

    def test_none_streams_become_empty_string(self, monkeypatch):
        monkeypatch.setattr(
            extract.subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout=None, stderr=None))
        assert extract.run("build_facts.py") == (0, "")


class TestLogGap:
    def test_shells_out_to_log_gap_with_fields(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(extract.subprocess, "run", fake_run)
        extract.log_gap("NFLX", "other", "fell back")
        cmd = captured["cmd"]
        assert cmd[1] == str(extract.SCRIPTS / "log_gap.py")
        assert cmd[cmd.index("--ticker") + 1] == "NFLX"
        assert cmd[cmd.index("--kind") + 1] == "other"
        assert cmd[cmd.index("--detail") + 1] == "fell back"


def seed_db(repo, ticker, facts=0, core=0, ddl=True):
    """Create research/{T}/Reports/{T}.duckdb with n facts / core rows."""
    d = repo / "research" / ticker / "Reports"
    d.mkdir(parents=True)
    con = duckdb.connect(str(d / f"{ticker}.duckdb"))
    if ddl:
        con.execute(schema.create_sql())
    for i in range(facts):
        con.execute(
            "INSERT INTO facts (metric, period, value_raw) VALUES ('revenue', ?, 1.0)",
            [f"FY{2020 + i}"])
    for i in range(core):
        con.execute(
            "INSERT INTO core_metrics (period) VALUES (?)", [f"FY{2020 + i}"])
    con.close()


class TestFactsCount:
    def test_missing_db_counts_zero(self, monkeypatch, tmp_path):
        monkeypatch.setattr(extract, "REPO", tmp_path)
        assert extract.facts_count("NFLX") == (0, 0)

    def test_counts_both_tables(self, monkeypatch, tmp_path):
        monkeypatch.setattr(extract, "REPO", tmp_path)
        seed_db(tmp_path, "NFLX", facts=3, core=2)
        assert extract.facts_count("NFLX") == (3, 2)

    def test_missing_core_metrics_table_counts_zero_core(self, monkeypatch, tmp_path):
        monkeypatch.setattr(extract, "REPO", tmp_path)
        d = tmp_path / "research" / "AGL.NZ" / "Reports"
        d.mkdir(parents=True)
        con = duckdb.connect(str(d / "AGL.NZ.duckdb"))
        con.execute("CREATE TABLE facts (metric TEXT)")
        con.execute("INSERT INTO facts VALUES ('revenue')")
        con.close()
        assert extract.facts_count("AGL.NZ") == (1, 0)

    def test_missing_facts_table_still_counts_core_rows(self, monkeypatch, tmp_path):
        # Each table is counted independently: the XBRL path never writes a
        # facts table, so a DB holding only core_metrics must not read as
        # (0, 0) — that would hide the core rows and make main() fall back.
        monkeypatch.setattr(extract, "REPO", tmp_path)
        d = tmp_path / "research" / "NFLX" / "Reports"
        d.mkdir(parents=True)
        con = duckdb.connect(str(d / "NFLX.duckdb"))
        con.execute("CREATE TABLE core_metrics (period TEXT)")
        con.execute("INSERT INTO core_metrics VALUES ('FY2024')")
        con.close()
        assert extract.facts_count("NFLX") == (0, 1)

    def test_unreadable_file_counts_zero(self, monkeypatch, tmp_path):
        monkeypatch.setattr(extract, "REPO", tmp_path)
        d = tmp_path / "research" / "NFLX" / "Reports"
        d.mkdir(parents=True)
        (d / "NFLX.duckdb").write_bytes(b"not a database")
        assert extract.facts_count("NFLX") == (0, 0)


def drive(monkeypatch, argv, results, counts):
    """Run main() with scripted child scripts and facts_count answers.

    results maps script name -> (rc, output); counts is consumed one tuple
    per facts_count call. An unscripted script raises KeyError, failing the
    test — no path runs that the scenario did not declare.
    """
    calls, gaps = [], []

    def fake_run(script, *args):
        calls.append((script, list(args)))
        return results[script]

    remaining = list(counts)
    monkeypatch.setattr(extract, "run", fake_run)
    monkeypatch.setattr(extract, "facts_count", lambda t: remaining.pop(0))
    monkeypatch.setattr(
        extract, "log_gap", lambda t, kind, detail: gaps.append((t, kind, detail)))
    monkeypatch.setattr(sys, "argv", ["extract.py", *argv])
    return extract.main(), calls, gaps


class TestMainRouting:
    def test_us_symbol_takes_xbrl_path(self, monkeypatch, capsys):
        rc, calls, gaps = drive(
            monkeypatch, ["NFLX"],
            {"build_facts_xbrl.py": (0, "built")}, [(0, 8)])
        assert rc == 0
        assert [c[0] for c in calls] == ["build_facts_xbrl.py"]
        assert gaps == []
        out = capsys.readouterr().out
        assert "SEC XBRL" in out
        assert "8 periods" in out

    def test_suffixed_symbol_goes_straight_to_text(self, monkeypatch, capsys):
        rc, calls, gaps = drive(
            monkeypatch, ["AGL.NZ"],
            {"build_facts.py": (0, "scanned"),
             "adjudicate.py": (0, "AGL.NZ: 9 core cells -- 5 resolved")},
            [(42, 0)])
        assert rc == 0
        # candidates are pre-adjudicated into the worksheet before any agent runs
        assert calls == [("build_facts.py", ["AGL.NZ"]), ("adjudicate.py", ["AGL.NZ"])]
        assert gaps == []
        out = capsys.readouterr().out
        assert "42 candidate facts" in out
        assert "5 resolved" in out

    def test_adjudicate_failure_is_reported_not_fatal(self, monkeypatch, capsys):
        rc, calls, _gaps = drive(
            monkeypatch, ["AGL.NZ"],
            {"build_facts.py": (0, "scanned"), "adjudicate.py": (2, "boom")},
            [(42, 0)])
        assert rc == 0
        assert [c[0] for c in calls] == ["build_facts.py", "adjudicate.py"]
        assert "WARNING: adjudicate.py exited 2" in capsys.readouterr().out

    def test_empty_xbrl_falls_back_to_text_and_logs_gap(self, monkeypatch, capsys):
        rc, calls, gaps = drive(
            monkeypatch, ["NFLX"],
            {"build_facts_xbrl.py": (0, "nothing"), "build_facts.py": (0, "ok"),
             "adjudicate.py": (0, "ok")},
            [(0, 0), (7, 0)])
        assert rc == 0
        assert [c[0] for c in calls] == ["build_facts_xbrl.py", "build_facts.py",
                                         "adjudicate.py"]
        assert [g[1] for g in gaps] == ["other"]
        assert "falling back to text" in capsys.readouterr().out

    def test_xbrl_nonzero_rc_with_core_rows_is_success(self, monkeypatch, capsys):
        # Populated core_metrics is the success signal, not the exit code: a
        # script that wrote rows but exited nonzero (say, one bad period) did
        # its job. Falling back to text would only bury the good rows, so
        # warn about the exit code and stop -- no fallback, no gap entry.
        rc, calls, gaps = drive(
            monkeypatch, ["NFLX"],
            {"build_facts_xbrl.py": (1, "boom")},
            [(0, 5)])
        assert rc == 0
        assert [c[0] for c in calls] == ["build_facts_xbrl.py"]
        assert gaps == []
        out = capsys.readouterr().out
        assert "SEC XBRL" in out
        assert "5 periods" in out
        assert "exited 1" in out  # the nonzero rc is surfaced, not swallowed

    def test_neither_path_logs_layout_unparsed_but_exits_zero(self, monkeypatch, capsys):
        rc, _calls, gaps = drive(
            monkeypatch, ["AGL.NZ"], {"build_facts.py": (0, "")}, [(0, 0)])
        assert rc == 0  # the agent reads filings directly; not a failure
        detail = ("neither XBRL nor text extraction produced facts; "
                  "agent fell back to reading filings directly")
        assert gaps == [("AGL.NZ", "layout_unparsed", detail)]
        assert "NEITHER PATH" in capsys.readouterr().out

    def test_forced_text_path_skips_xbrl_for_us_symbol(self, monkeypatch, capsys):
        rc, calls, _ = drive(
            monkeypatch, ["NFLX", "--path", "text"],
            {"build_facts.py": (0, "ok"), "adjudicate.py": (0, "ok")}, [(5, 0)])
        assert rc == 0
        assert [c[0] for c in calls] == ["build_facts.py", "adjudicate.py"]

    def test_show_flag_forwarded_to_child_script(self, monkeypatch, capsys):
        _, calls, _ = drive(
            monkeypatch, ["NFLX", "--show"],
            {"build_facts_xbrl.py": (0, "ok")}, [(0, 3)])
        assert calls == [("build_facts_xbrl.py", ["NFLX", "--show"])]


class TestXbrlCrashIsDistinguishedFromNoCoverage:
    """A crash in the XBRL path must not be reported as absent SEC coverage.

    ADBE hit a BinderException writing core_metrics (stale DB schema). The
    fallback logged the same "XBRL yielded nothing" message it uses for a
    genuine foreign-private-issuer miss, so a US filer with perfectly good
    XBRL silently went through text extraction instead. The two need
    different messages and different gap kinds, or the bug is invisible.
    """

    def test_traceback_in_output_is_reported_as_a_crash(self, tmp_path, capsys, monkeypatch):
        import extract

        monkeypatch.setattr(extract, "facts_count", lambda t: (0, 0))
        monkeypatch.setattr(extract, "is_us_symbol", lambda t: True)
        logged = []
        monkeypatch.setattr(extract, "log_gap",
                            lambda t, kind, msg: logged.append((kind, msg)))

        calls = []

        def fake_run(script, ticker, *a):
            calls.append(script)
            if script == "build_facts_xbrl.py":
                return 1, ('ADBE: CIK 796343\n  109 periods, 20 concepts matched\n'
                           'Traceback (most recent call last):\n'
                           'duckdb.duckdb.BinderException: Binder Error: Table '
                           '"core_metrics" does not have a column with name "x"')
            return 0, ""

        monkeypatch.setattr(extract, "run", fake_run)
        monkeypatch.setattr(sys, "argv", ["extract.py", "ADBE"])
        extract.main()

        out = capsys.readouterr().out
        assert "crash" in out.lower()
        assert "BinderException" in out or "Binder Error" in out
        assert logged
        assert logged[0][0] == "extractor_bug"
