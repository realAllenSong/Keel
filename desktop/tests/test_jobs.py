"""No network, GUI or real data directories are used by these job checks."""

import datetime as dt

from ledger_app.jobs import Jobs


def test_daily_and_monthly_attempts_survive_restart_even_after_failure(tmp_path, monkeypatch):
    class FirstMorning(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 1, 8, 0, tzinfo=tz)

    monkeypatch.setattr("ledger_app.jobs.dt.datetime", FirstMorning)
    monkeypatch.setenv("PF_DATA_DIR", str(tmp_path))
    calls = []

    def fail_daily():
        calls.append("daily")
        raise RuntimeError("Provider unavailable")

    for _ in range(2):
        jobs = Jobs()
        monkeypatch.setattr(jobs, "daily", fail_daily)
        monkeypatch.setattr(jobs, "monthly", lambda: calls.append("monthly"))
        monkeypatch.setattr(jobs.stop, "wait", lambda seconds, current=jobs: current.stop.set())
        jobs.schedule()
    assert calls == ["daily", "monthly"]


def test_daily_backup_runs_when_market_update_fails(monkeypatch):
    calls = []

    def offline(conn):
        raise OSError("Network unavailable")

    from contextlib import contextmanager

    @contextmanager
    def transaction():
        yield object()

    monkeypatch.setattr("pf.db.transaction", transaction)
    monkeypatch.setattr("pf.market.update.update_market", offline)
    monkeypatch.setattr("pf.ops.backup.chosen_dir", lambda conn: None)  # default backup folder
    monkeypatch.setattr("pf.ops.backup.run_backup", lambda dest: calls.append("backup"))
    Jobs.daily()
    assert calls == ["backup"]


def test_app_and_background_agent_never_run_the_daily_job_twice(tmp_path, monkeypatch):
    """The LaunchAgent and the open app share the markers under a lock: one runs, one skips."""
    import fcntl

    monkeypatch.setenv("PF_DATA_DIR", str(tmp_path))
    morning = dt.datetime(2026, 11, 14, 7, 45).astimezone()
    calls = []
    app, agent = Jobs(), Jobs()
    for jobs in (app, agent):
        monkeypatch.setattr(jobs, "daily", lambda: calls.append("daily"))

    # While the agent holds the lock (mid-sync), the app's scheduler skips this round.
    with (tmp_path / "desktop-jobs.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert app.run_due(morning) == []
    assert agent.run_due(morning) == ["daily"]
    assert app.run_due(morning) == []  # re-reads the marker: today's run is done
    assert app.run_due(morning.replace(hour=7, minute=0) + dt.timedelta(days=1)) == []  # before 7:30
    assert app.run_due(morning + dt.timedelta(days=1)) == ["daily"]
    assert calls == ["daily", "daily"]
