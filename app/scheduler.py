"""Weekly automatic firmware refresh - runs the exact same job as the
"Refresh firmware from BKC page" button, on a fixed schedule (default:
every Sunday at 01:00, server-local time), with no user interaction.

Implemented as a simple polling loop rather than a cron library (keeps
this dependency-free): every POLL_INTERVAL_SECONDS it just checks "is it
currently the scheduled day/hour, and have I not already run this ISO
week" - this is resilient to server restarts (a precomputed single long
sleep would be reset by a restart; this just re-checks against real time).

Runs as a background asyncio task started from main.py's startup hook, not
a separate process - it reuses the running app's own JobManager so the
scheduled run shows up through the same job-tracking machinery as any
other job, just under a reserved pseudo-user ("scheduler") that no real
Entra ID account can ever collide with (real oid claims are GUIDs).
"""

import asyncio
import json
import sys
from datetime import datetime

from .jobs import JobManager

SCHEDULE_WEEKDAY = 6  # datetime.weekday(): Monday=0 ... Sunday=6
SCHEDULE_HOUR = 1  # 1am, server-local time (whatever timezone the OS/process is set to)
POLL_INTERVAL_SECONDS = 60
SCHEDULER_OWNER = "scheduler"


def _iso_week(dt: datetime) -> list:
    iso = dt.isocalendar()
    return [iso.year, iso.week]


class WeeklyRefreshScheduler:
    """Owns the state file path and the actual poll loop. Takes
    build_cmd() as a parameter (rather than importing main.py) so the
    scheduled run is built from the EXACT same command-construction logic
    as the manual button - the two triggers can never drift apart."""

    def __init__(self, state_file, jobs: JobManager, build_cmd):
        self.state_file = state_file
        self.jobs = jobs
        self.build_cmd = build_cmd

    def _load_last_run_week(self):
        try:
            return json.loads(self.state_file.read_text())["last_run_iso_week"]
        except Exception:
            return None

    def _save_last_run(self, iso_week, status):
        self.state_file.write_text(json.dumps({
            "last_run_iso_week": iso_week,
            "last_run_at": datetime.now().isoformat(),
            "last_run_status": status,
        }))

    def get_last_run_info(self):
        try:
            return json.loads(self.state_file.read_text())
        except Exception:
            return None

    async def run_forever(self):
        last_run_week = self._load_last_run_week()
        print(
            f"[scheduler] Weekly firmware refresh armed - every "
            f"{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][SCHEDULE_WEEKDAY]} at "
            f"{SCHEDULE_HOUR:02d}:00 server-local time.",
            file=sys.stderr,
        )
        while True:
            now = datetime.now()
            current_week = _iso_week(now)
            if now.weekday() == SCHEDULE_WEEKDAY and now.hour == SCHEDULE_HOUR and current_week != last_run_week:
                print(f"[scheduler] Starting weekly firmware refresh ({now.isoformat()}) ...", file=sys.stderr)
                job = self.jobs.start(self.build_cmd(), owner=SCHEDULER_OWNER, kind="refresh")
                while not job.done:
                    await asyncio.sleep(2)
                status = "ok" if job.exit_code == 0 else f"failed (exit {job.exit_code})"
                print(f"[scheduler] Weekly firmware refresh finished: {status}", file=sys.stderr)
                last_run_week = current_week
                self._save_last_run(current_week, status)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
