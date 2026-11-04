"""Unit tests for app.scheduler.WeeklyRefreshScheduler - no real waiting,
no real subprocess; drives the trigger logic directly with a fake clock
and a fake JobManager."""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DEV_BYPASS_AUTH", "1")
os.environ.setdefault("BIND_HOST", "127.0.0.1")

from app import scheduler as scheduler_module  # noqa: E402


class FakeJob:
    def __init__(self, exit_code):
        self.done = True
        self.exit_code = exit_code


class FakeJobManager:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.started_cmds = []

    def start(self, cmd, owner, kind):
        self.started_cmds.append((cmd, owner, kind))
        return FakeJob(self.exit_code)


async def _run_one_poll_iteration(sched):
    """Runs the body of run_forever()'s while-loop exactly once, by
    patching asyncio.sleep to raise after the first call - avoids needing
    run_forever() itself to support a 'run N iterations' mode."""
    call_count = 0

    async def fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise asyncio.CancelledError()

    with patch("app.scheduler.asyncio.sleep", fake_sleep):
        try:
            await sched.run_forever()
        except asyncio.CancelledError:
            pass


def test_triggers_on_sunday_1am_when_not_already_run_this_week():
    with tempfile.TemporaryDirectory() as d:
        state_file = Path(d) / "state.json"
        jobs = FakeJobManager(exit_code=0)
        sched = scheduler_module.WeeklyRefreshScheduler(state_file, jobs, lambda: ["echo", "refresh"])

        sunday_1am = datetime(2026, 11, 8, 1, 0)  # a real Sunday
        assert sunday_1am.weekday() == 6, "test fixture date must actually be a Sunday"

        with patch("app.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = sunday_1am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            asyncio.run(_run_one_poll_iteration(sched))

        assert len(jobs.started_cmds) == 1, jobs.started_cmds
        assert jobs.started_cmds[0] == (["echo", "refresh"], "scheduler", "refresh")
        info = json.loads(state_file.read_text())
        assert info["last_run_status"] == "ok", info
        print("PASS: triggers the refresh job when it's Sunday 1am and no run recorded for this ISO week")


def test_does_not_trigger_twice_in_the_same_week():
    with tempfile.TemporaryDirectory() as d:
        state_file = Path(d) / "state.json"
        jobs = FakeJobManager(exit_code=0)
        sched = scheduler_module.WeeklyRefreshScheduler(state_file, jobs, lambda: ["echo", "refresh"])

        sunday_1am = datetime(2026, 11, 8, 1, 0)
        with patch("app.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = sunday_1am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            asyncio.run(_run_one_poll_iteration(sched))  # first run: triggers

        jobs2 = FakeJobManager(exit_code=0)
        sched2 = scheduler_module.WeeklyRefreshScheduler(state_file, jobs2, lambda: ["echo", "refresh"])
        sunday_1am_5min_later = datetime(2026, 11, 8, 1, 5)
        with patch("app.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = sunday_1am_5min_later
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            asyncio.run(_run_one_poll_iteration(sched2))  # should NOT trigger again

        assert len(jobs2.started_cmds) == 0, jobs2.started_cmds
        print("PASS: does not re-trigger on a later poll within the same ISO week (e.g. a server restart mid-window)")


def test_does_not_trigger_on_a_different_day_or_hour():
    with tempfile.TemporaryDirectory() as d:
        state_file = Path(d) / "state.json"
        jobs = FakeJobManager(exit_code=0)
        sched = scheduler_module.WeeklyRefreshScheduler(state_file, jobs, lambda: ["echo", "refresh"])

        monday_1am = datetime(2026, 11, 9, 1, 0)  # Monday, not Sunday
        with patch("app.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = monday_1am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            asyncio.run(_run_one_poll_iteration(sched))
        assert len(jobs.started_cmds) == 0, jobs.started_cmds

        sunday_3pm = datetime(2026, 11, 8, 15, 0)  # Sunday, wrong hour
        with patch("app.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = sunday_3pm
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            asyncio.run(_run_one_poll_iteration(sched))
        assert len(jobs.started_cmds) == 0, jobs.started_cmds
        print("PASS: does not trigger on the wrong weekday or the wrong hour")


def test_records_failure_status():
    with tempfile.TemporaryDirectory() as d:
        state_file = Path(d) / "state.json"
        jobs = FakeJobManager(exit_code=1)
        sched = scheduler_module.WeeklyRefreshScheduler(state_file, jobs, lambda: ["echo", "refresh"])

        sunday_1am = datetime(2026, 11, 8, 1, 0)
        with patch("app.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = sunday_1am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            asyncio.run(_run_one_poll_iteration(sched))

        info = json.loads(state_file.read_text())
        assert info["last_run_status"] == "failed (exit 1)", info
        print("PASS: a failed scheduled refresh records its failure status (visible on the page) instead of looking silent")


if __name__ == "__main__":
    test_triggers_on_sunday_1am_when_not_already_run_this_week()
    test_does_not_trigger_twice_in_the_same_week()
    test_does_not_trigger_on_a_different_day_or_hour()
    test_records_failure_status()
    print("\nALL PASS")
