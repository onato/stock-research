"""git_lock.py decides whether a held lock is live or an orphan.

`state/git.lock.d` sat orphaned from 10-Aug 21:34 until it was cleared by
hand two days later. It was created by a worker that the rate-limit abort
batch tore down mid-commit, and because the directory was empty nothing could
tell a live holder from a corpse. Every runner since waited the full ten
minutes and gave up with "Could not acquire git lock after 10m; skipping
commit" -- so completed research went uncommitted, and GXH.NZ and HGH.NZ sat
in history as "missing Dashboard.html" while both dashboards were on disk.

mkdir stays the atomic acquire (stock macOS has no flock(1)). What this adds
is an owner record, so a waiter can reclaim a lock whose owner is gone
instead of blocking on it forever.

The rule that matters: only ever reclaim or release a lock we can *prove* is
not someone else's. A false positive here deletes a sibling's live lock and
corrupts two concurrent commits.
"""

import os

import git_lock


def make_lock(tmp_path, pid=None, ticker="TEST.NZ"):
    d = tmp_path / "git.lock.d"
    d.mkdir()
    if pid is not None:
        (d / "owner").write_text(f"{pid} {ticker}\n")
    return d


class TestOwner:
    def test_reads_back_the_recorded_owner(self, tmp_path):
        d = make_lock(tmp_path, pid=4242, ticker="AIA.NZ")
        assert git_lock.owner(d) == (4242, "AIA.NZ")

    def test_a_lock_with_no_owner_file_has_no_owner(self, tmp_path):
        # Exactly the orphan that caused this: an empty lock directory.
        d = make_lock(tmp_path)
        assert git_lock.owner(d) is None

    def test_a_garbled_owner_file_has_no_owner(self, tmp_path):
        d = make_lock(tmp_path)
        (d / "owner").write_text("not-a-pid\n")
        assert git_lock.owner(d) is None

    def test_a_pid_without_a_ticker_still_parses(self, tmp_path):
        d = make_lock(tmp_path)
        (d / "owner").write_text("777\n")
        assert git_lock.owner(d) == (777, "")


class TestStaleness:
    def test_a_lock_owned_by_a_dead_pid_is_stale(self, tmp_path):
        # A PID that cannot exist: reclaim immediately rather than waiting.
        d = make_lock(tmp_path, pid=999_999)
        assert git_lock.is_stale(d) is True

    def test_a_lock_owned_by_a_live_pid_is_not_stale(self, tmp_path):
        # Never steal from a running worker: that corrupts two commits.
        d = make_lock(tmp_path, pid=os.getpid())
        assert git_lock.is_stale(d) is False

    def test_an_ownerless_lock_is_stale(self, tmp_path):
        # The real orphan had no owner file at all. If this is not
        # reclaimable, the deadlock needs manual intervention -- which is
        # what happened for two days.
        d = make_lock(tmp_path)
        assert git_lock.is_stale(d) is True

    def test_a_missing_lock_is_not_stale(self, tmp_path):
        # Nothing to reclaim; the caller should just try to acquire.
        assert git_lock.is_stale(tmp_path / "absent.lock.d") is False


class TestReclaim:
    def test_reclaiming_removes_a_stale_lock(self, tmp_path):
        d = make_lock(tmp_path, pid=999_999)
        assert git_lock.reclaim(d) is True
        assert not d.exists()

    def test_reclaiming_leaves_a_live_lock_alone(self, tmp_path):
        d = make_lock(tmp_path, pid=os.getpid())
        assert git_lock.reclaim(d) is False
        assert d.exists()

    def test_reclaiming_a_missing_lock_is_a_no_op(self, tmp_path):
        assert git_lock.reclaim(tmp_path / "absent.lock.d") is False

    def test_a_lock_holding_extra_files_is_still_removed(self, tmp_path):
        # rmdir would fail on a non-empty directory; the orphan may hold an
        # owner file, so removal has to be recursive.
        d = make_lock(tmp_path, pid=999_999)
        (d / "stray") .write_text("x")
        assert git_lock.reclaim(d) is True
        assert not d.exists()


class TestRelease:
    def test_releases_a_lock_we_own(self, tmp_path):
        d = make_lock(tmp_path, pid=os.getpid())
        assert git_lock.release(d, os.getpid()) is True
        assert not d.exists()

    def test_refuses_to_release_someone_elses_lock(self, tmp_path):
        # The trap runs in every worker. Releasing on exit without checking
        # ownership would let a dying worker unlock a sibling's commit.
        d = make_lock(tmp_path, pid=os.getpid() + 1)
        assert git_lock.release(d, os.getpid()) is False
        assert d.exists()

    def test_refuses_to_release_an_ownerless_lock(self, tmp_path):
        # Cannot prove it is ours, so leave it: is_stale()/reclaim() is the
        # deliberate path for those, not an exit trap.
        d = make_lock(tmp_path)
        assert git_lock.release(d, os.getpid()) is False
        assert d.exists()

    def test_releasing_a_missing_lock_is_a_no_op(self, tmp_path):
        assert git_lock.release(tmp_path / "absent.lock.d", os.getpid()) is False


class TestLiveness:
    """Signal-0 probing, including the cases that are not simple yes/no."""

    def test_an_empty_owner_file_has_no_owner(self, tmp_path):
        d = make_lock(tmp_path)
        (d / "owner").write_text("")
        assert git_lock.owner(d) is None

    def test_a_permission_error_counts_as_alive(self, tmp_path, monkeypatch):
        # A lock held by another user exists; treating it as dead would let
        # us steal a live lock.
        def denied(pid, sig):
            raise PermissionError

        monkeypatch.setattr(git_lock.os, "kill", denied)
        d = make_lock(tmp_path, pid=4242)
        assert git_lock.is_stale(d) is False

    def test_an_unexpected_oserror_counts_as_dead(self, tmp_path, monkeypatch):
        # Better to reclaim than to deadlock the queue for two days.
        def broken(pid, sig):
            raise OSError("weird")

        monkeypatch.setattr(git_lock.os, "kill", broken)
        d = make_lock(tmp_path, pid=4242)
        assert git_lock.is_stale(d) is True


class TestCli:
    def _run(self, monkeypatch, capsys, *argv):
        monkeypatch.setattr("sys.argv", ["git_lock.py", *argv])
        return git_lock.main()

    def test_check_exits_zero_when_stale(self, tmp_path, monkeypatch, capsys):
        # Exit 0 is the shell's "yes, reclaim it".
        d = make_lock(tmp_path, pid=999_999)
        assert self._run(monkeypatch, capsys, "--check", str(d)) == 0

    def test_check_exits_one_when_live(self, tmp_path, monkeypatch, capsys):
        d = make_lock(tmp_path, pid=os.getpid())
        assert self._run(monkeypatch, capsys, "--check", str(d)) == 1

    def test_check_reports_the_owner_for_the_wait_message(self, tmp_path,
                                                         monkeypatch, capsys):
        # "waiting on PID 123 (AIA.NZ)" is diagnosable; "could not acquire"
        # after ten minutes is not.
        d = make_lock(tmp_path, pid=os.getpid(), ticker="AIA.NZ")
        self._run(monkeypatch, capsys, "--check", str(d))
        out = capsys.readouterr().out
        assert "AIA.NZ" in out
        assert str(os.getpid()) in out

    def test_reclaim_removes_a_stale_lock(self, tmp_path, monkeypatch, capsys):
        d = make_lock(tmp_path, pid=999_999)
        assert self._run(monkeypatch, capsys, "--reclaim", str(d)) == 0
        assert not d.exists()

    def test_reclaim_refuses_a_live_lock(self, tmp_path, monkeypatch, capsys):
        d = make_lock(tmp_path, pid=os.getpid())
        assert self._run(monkeypatch, capsys, "--reclaim", str(d)) == 1
        assert d.exists()

    def test_release_requires_a_pid(self, tmp_path, monkeypatch, capsys):
        d = make_lock(tmp_path, pid=os.getpid())
        assert self._run(monkeypatch, capsys, "--release", str(d),
                         "--pid", str(os.getpid())) == 0
        assert not d.exists()

    def test_no_arguments_is_a_usage_error(self, monkeypatch, capsys):
        assert self._run(monkeypatch, capsys) == 2
        assert "usage" in capsys.readouterr().err.lower()

    def test_check_reports_an_ownerless_lock(self, tmp_path, monkeypatch,
                                             capsys):
        # The 10-Aug orphan: no owner file, so say so rather than printing a
        # confusing partial line.
        d = make_lock(tmp_path)
        assert self._run(monkeypatch, capsys, "--check", str(d)) == 0
        assert "no owner recorded" in capsys.readouterr().out

    def test_check_reports_a_dead_owner_as_dead(self, tmp_path, monkeypatch,
                                                capsys):
        d = make_lock(tmp_path, pid=999_999, ticker="OLD.NZ")
        self._run(monkeypatch, capsys, "--check", str(d))
        out = capsys.readouterr().out
        assert "dead" in out
        assert "OLD.NZ" in out

    def test_release_without_a_pid_is_a_usage_error(self, tmp_path,
                                                    monkeypatch, capsys):
        d = make_lock(tmp_path, pid=os.getpid())
        assert self._run(monkeypatch, capsys, "--release", str(d)) == 2
        assert "needs --pid" in capsys.readouterr().err
        assert d.exists()

    def test_release_of_someone_elses_lock_exits_one(self, tmp_path,
                                                     monkeypatch, capsys):
        d = make_lock(tmp_path, pid=os.getpid() + 1)
        assert self._run(monkeypatch, capsys, "--release", str(d),
                         "--pid", str(os.getpid())) == 1
        assert d.exists()
