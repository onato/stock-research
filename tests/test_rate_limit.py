"""rate_limit.py decides whether a run must wait, and for how long.

The distinction that matters is `status`. A live run logs three kinds of
rate_limit_event, and only one of them means requests are being refused:

    allowed            25 seen -- fine
    allowed_warning    32 seen -- "you are at 79% of the seven-day quota",
                       requests still succeed
    rejected           10 seen -- actually blocked

Treating `allowed_warning` as a block cost two hours per ticker: the seven-day
window's resetsAt is days away, so the wait clamped to the 2h ceiling and the
loop parked itself on a warning while quota remained. That is the bug these
tests exist to prevent coming back.
"""

import json
import sys
import time

import pytest
import rate_limit


def event(status, resets_in=3600, kind="five_hour"):
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "rateLimitType": kind,
            "resetsAt": time.time() + resets_in,
        },
    }


def write_log(tmp_path, *events):
    path = tmp_path / "T.log"
    with open(path, "w") as f:
        f.writelines(
            (json.dumps(e) if isinstance(e, dict) else e) + "\n"
            for e in events)
    return path


class TestBlocking:
    def test_a_rejection_blocks(self, tmp_path):
        log = write_log(tmp_path, event("rejected", resets_in=600))
        assert rate_limit.seconds_to_wait(log) > 0

    def test_a_warning_does_not_block(self, tmp_path):
        # "allowed_warning" means the request went through. The seven-day
        # resetsAt is days out, so honouring it parks the loop for hours.
        log = write_log(tmp_path,
                        event("allowed_warning", resets_in=5 * 86400,
                              kind="seven_day"))
        assert rate_limit.seconds_to_wait(log) == 0

    def test_allowed_does_not_block(self, tmp_path):
        log = write_log(tmp_path, event("allowed"))
        assert rate_limit.seconds_to_wait(log) == 0

    def test_a_warning_beside_a_rejection_still_blocks(self, tmp_path):
        # A run logs several events; one genuine rejection is enough.
        log = write_log(tmp_path,
                        event("allowed_warning", resets_in=5 * 86400,
                              kind="seven_day"),
                        event("rejected", resets_in=600))
        assert rate_limit.seconds_to_wait(log) > 0

    def test_the_furthest_rejection_reset_wins(self, tmp_path):
        log = write_log(tmp_path,
                        event("rejected", resets_in=300),
                        event("rejected", resets_in=900))
        assert rate_limit.seconds_to_wait(log) > 800

    def test_only_a_rejections_reset_is_used(self, tmp_path):
        # A warning's far-future reset must not extend a real wait.
        log = write_log(tmp_path,
                        event("rejected", resets_in=300),
                        event("allowed_warning", resets_in=5 * 86400,
                              kind="seven_day"))
        assert rate_limit.seconds_to_wait(log) < 1000


class TestUnreachableResets:
    """A reset beyond the cap cannot be waited out inside one run.

    The weekly window resets days away. Sleeping the 2h cap and retrying just
    re-rejects: CCC.NZ ran 8.4 hours and cost $11.96 for ~40 minutes of work,
    because the retry redid a ticker that had already produced every
    deliverable. The run has to stop and be resumed after the reset instead.
    """

    def test_a_reset_beyond_the_cap_is_unreachable(self, tmp_path):
        log = write_log(tmp_path,
                        event("rejected", resets_in=4 * 86400,
                              kind="seven_day_overage_included"))
        assert rate_limit.is_unreachable(log, cap=7200) is True

    def test_a_reset_within_the_cap_is_reachable(self, tmp_path):
        # The five-hour window: 106 minutes is a real wait worth taking.
        log = write_log(tmp_path, event("rejected", resets_in=6400))
        assert rate_limit.is_unreachable(log, cap=7200) is False

    def test_no_rejection_is_not_unreachable(self, tmp_path):
        log = write_log(tmp_path, event("allowed_warning", kind="seven_day"))
        assert rate_limit.is_unreachable(log) is False

    def test_a_missing_log_is_not_unreachable(self, tmp_path):
        assert rate_limit.is_unreachable(tmp_path / "nope.log") is False

    def test_reset_time_is_reported_for_the_message(self, tmp_path):
        when = time.time() + 4 * 86400
        log = write_log(tmp_path,
                        {"type": "rate_limit_event",
                         "rate_limit_info": {"status": "rejected",
                                             "resetsAt": when}})
        got = rate_limit.reset_at(log)
        assert got is not None
        assert abs(got - when) < 1

    def test_reset_time_is_none_without_a_rejection(self, tmp_path):
        log = write_log(tmp_path, event("allowed"))
        assert rate_limit.reset_at(log) is None


class TestClamping:
    def test_the_wait_is_capped(self, tmp_path):
        log = write_log(tmp_path, event("rejected", resets_in=10 * 3600))
        assert rate_limit.seconds_to_wait(log, cap=7200) == 7200

    def test_a_past_reset_yields_no_wait(self, tmp_path):
        log = write_log(tmp_path, event("rejected", resets_in=-600))
        assert rate_limit.seconds_to_wait(log) == 0

    def test_slack_is_added_so_we_do_not_wake_on_the_boundary(self, tmp_path):
        log = write_log(tmp_path, event("rejected", resets_in=100))
        assert rate_limit.seconds_to_wait(log) > 100


class TestMalformedInput:
    def test_a_missing_log_yields_no_wait(self, tmp_path):
        assert rate_limit.seconds_to_wait(tmp_path / "nope.log") == 0

    def test_non_json_lines_are_skipped(self, tmp_path):
        log = write_log(tmp_path, "not json at all",
                        event("rejected", resets_in=600))
        assert rate_limit.seconds_to_wait(log) > 0

    def test_an_event_without_a_reset_is_ignored(self, tmp_path):
        log = write_log(tmp_path, {"type": "rate_limit_event",
                                   "rate_limit_info": {"status": "rejected"}})
        assert rate_limit.seconds_to_wait(log) == 0

    def test_other_event_types_are_ignored(self, tmp_path):
        log = write_log(tmp_path, {"type": "assistant", "message": {}})
        assert rate_limit.seconds_to_wait(log) == 0

    def test_a_malformed_json_line_is_skipped(self, tmp_path):
        # A truncated line -- a run killed mid-write -- must not lose the
        # rejection recorded after it.
        log = write_log(tmp_path,
                        '{"type": "rate_limit_event", "rate_limit_',
                        event("rejected", resets_in=600))
        assert rate_limit.seconds_to_wait(log) > 0

    def test_a_line_mentioning_the_event_but_of_another_type_is_skipped(
            self, tmp_path):
        # Prose containing "rate_limit_event" passes the cheap substring
        # prefilter; the type check is what actually decides.
        log = write_log(tmp_path,
                        {"type": "assistant",
                         "text": "saw a rate_limit_event earlier"})
        assert rate_limit.seconds_to_wait(log) == 0

    def test_an_unreadable_log_yields_no_wait(self, tmp_path):
        # A directory where a log is expected: never fatal.
        d = tmp_path / "T.log"
        d.mkdir()
        assert rate_limit.seconds_to_wait(d) == 0

    def test_a_boolean_reset_is_not_a_timestamp(self, tmp_path):
        # bool is a subclass of int; True must not be read as epoch 1.
        log = write_log(tmp_path,
                        {"type": "rate_limit_event",
                         "rate_limit_info": {"status": "rejected",
                                             "resetsAt": True}})
        assert rate_limit.seconds_to_wait(log) == 0

    def test_a_status_free_event_is_ignored(self, tmp_path):
        log = write_log(tmp_path,
                        {"type": "rate_limit_event",
                         "rate_limit_info": {"resetsAt": time.time() + 600}})
        assert rate_limit.seconds_to_wait(log) == 0


class TestCli:
    def _run(self, monkeypatch, capsys, *argv):
        monkeypatch.setattr(sys, "argv", ["rate_limit.py", *argv])
        rate_limit.main()
        return capsys.readouterr().out.strip()

    def test_prints_the_wait_in_seconds(self, tmp_path, monkeypatch, capsys):
        log = write_log(tmp_path, event("rejected", resets_in=600))
        assert int(self._run(monkeypatch, capsys, str(log))) > 0

    def test_prints_zero_for_a_warning(self, tmp_path, monkeypatch, capsys):
        log = write_log(tmp_path, event("allowed_warning", kind="seven_day"))
        assert self._run(monkeypatch, capsys, str(log)) == "0"

    def test_honours_a_cap_argument(self, tmp_path, monkeypatch, capsys):
        log = write_log(tmp_path, event("rejected", resets_in=10 * 3600))
        assert self._run(monkeypatch, capsys, str(log), "60") == "60"

    def test_a_bad_cap_falls_back_to_the_default(self, tmp_path, monkeypatch,
                                                 capsys):
        log = write_log(tmp_path, event("rejected", resets_in=10 * 3600))
        got = int(self._run(monkeypatch, capsys, str(log), "not-a-number"))
        assert got == pytest.approx(7200, abs=60)

    def test_no_arguments_is_a_usage_error(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["rate_limit.py"])
        assert rate_limit.main() == 2
        assert "usage:" in capsys.readouterr().err

    def test_unreachable_exits_zero_and_prints_the_reset(self, tmp_path,
                                                         monkeypatch, capsys):
        # Exit 0 is the shell's signal to stop the run.
        log = write_log(tmp_path, event("rejected", resets_in=4 * 86400,
                                        kind="seven_day"))
        monkeypatch.setattr(sys, "argv",
                            ["rate_limit.py", "--unreachable", str(log)])
        assert rate_limit.main() == 0
        assert int(capsys.readouterr().out.strip()) > time.time()

    def test_a_reachable_reset_exits_one(self, tmp_path, monkeypatch, capsys):
        log = write_log(tmp_path, event("rejected", resets_in=600))
        monkeypatch.setattr(sys, "argv",
                            ["rate_limit.py", "--unreachable", str(log)])
        assert rate_limit.main() == 1

    def test_no_rejection_exits_one(self, tmp_path, monkeypatch, capsys):
        log = write_log(tmp_path, event("allowed_warning", kind="seven_day"))
        monkeypatch.setattr(sys, "argv",
                            ["rate_limit.py", "--unreachable", str(log)])
        assert rate_limit.main() == 1

    def test_unreachable_honours_an_explicit_cap(self, tmp_path, monkeypatch,
                                                 capsys):
        # 90 minutes out: unreachable under a 60s cap, reachable under 2h.
        log = write_log(tmp_path, event("rejected", resets_in=5400))
        monkeypatch.setattr(sys, "argv",
                            ["rate_limit.py", "--unreachable", str(log), "60"])
        assert rate_limit.main() == 0
        monkeypatch.setattr(sys, "argv",
                            ["rate_limit.py", "--unreachable", str(log), "7200"])
        assert rate_limit.main() == 1


class TestResetAtEdges:
    def test_an_unreadable_log_has_no_reset(self, tmp_path):
        d = tmp_path / "T.log"
        d.mkdir()
        assert rate_limit.reset_at(d) is None

    def test_a_malformed_line_is_skipped(self, tmp_path):
        log = write_log(tmp_path, '{"type": "rate_limit_event", "trunc',
                        event("rejected", resets_in=600))
        assert rate_limit.reset_at(log) is not None

    def test_another_event_type_is_ignored(self, tmp_path):
        log = write_log(tmp_path,
                        {"type": "assistant",
                         "text": "mentions rate_limit_event in prose"})
        assert rate_limit.reset_at(log) is None

    def test_a_boolean_reset_is_rejected(self, tmp_path):
        log = write_log(tmp_path,
                        {"type": "rate_limit_event",
                         "rate_limit_info": {"status": "rejected",
                                             "resetsAt": True}})
        assert rate_limit.reset_at(log) is None

    def test_the_furthest_reset_is_reported(self, tmp_path):
        log = write_log(tmp_path,
                        event("rejected", resets_in=300),
                        event("rejected", resets_in=900))
        got = rate_limit.reset_at(log)
        assert got is not None
        assert got > time.time() + 800
