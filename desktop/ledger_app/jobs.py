"""Best-effort jobs with durable scheduling markers and graceful shutdown."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class Jobs:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for name, target in (("inbox", self.watch), ("schedule", self.schedule)):
            thread = threading.Thread(target=target, name=f"keel-{name}", daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=3)

    def watch(self) -> None:
        from pf.ops.inbox import watch

        while not self.stop.is_set():
            try:
                watch(stop=self.stop)
            except Exception:
                log.exception("Inbox watcher failed; retrying in one minute")
                self.stop.wait(60)

    def schedule(self) -> None:
        while not self.stop.is_set():
            try:
                self.run_due()
            except Exception:
                log.exception("Scheduler failed")
            self.stop.wait(30)

    def run_due(self, now: dt.datetime | None = None) -> list[str]:
        """Run the daily (after 7:30) and monthly (on the 1st) jobs not yet run.

        Shared by the open app's scheduler and the LaunchAgent (`keel-engine background`): an
        exclusive lock means one process works at a time, and the markers are re-read under it,
        so whichever comes first runs today's job and the other sees it done."""
        import fcntl

        marker = Path(os.environ["PF_DATA_DIR"]) / "desktop-jobs.json"
        with marker.with_suffix(".lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return []  # the other process is running them right now
            try:
                state = json.loads(marker.read_text()) if marker.exists() else {}
            except (OSError, ValueError):
                log.exception("Unable to read job markers")
                state = {}
            now = now or dt.datetime.now().astimezone()
            due = []
            if (now.hour, now.minute) >= (7, 30) and state.get("daily") != now.date().isoformat():
                due.append(("daily", now.date().isoformat(), self.daily))
            if now.day == 1 and state.get("monthly") != now.strftime("%Y-%m"):
                due.append(("monthly", now.strftime("%Y-%m"), self.monthly))
            for name, stamp, job in due:
                # Mark attempts before execution: restarts do not hammer failed providers.
                state[name] = stamp
                partial = marker.with_suffix(".tmp")
                partial.write_text(json.dumps(state))
                partial.replace(marker)
                try:
                    job()
                except Exception:
                    log.exception("%s job failed", name)
            return [name for name, _, _ in due]

    @staticmethod
    def daily() -> None:
        from pf.db import transaction
        from pf.market.update import update_market
        from pf.ops.backup import run_backup

        try:
            with transaction() as conn:
                result = update_market(conn)
            for error in result.errors:
                log.error("Market update: %s", error)
        except Exception:
            log.exception("Market update failed")
        try:  # banks, brokers, exchanges linked in Settings → 连接 (each one fails on its own)
            from pf.connectors import sync_now

            for r in sync_now():
                log.info("Sync %s: ok=%s new=%s %s", r.source, r.ok, r.new, r.message or "")
        except Exception:
            log.exception("Account sync failed")
        try:  # a finished month with everything in and agreeing closes by itself
            from pf.i18n import tr
            from pf.ledger.close import auto_close
            from pf.ops.notify import notify
            from pf.prefs import load_prefs, use_saved_language

            with transaction() as conn:
                prefs = load_prefs(conn)
                closed = auto_close(conn) if prefs.auto_close else []
                use_saved_language(conn)
            for c in closed:
                log.info("Closed %s: income %s, expenses %s", c.month, c.income, c.expenses)
                if prefs.alerts and c.closed:
                    sign = {"USD": "$", "CNY": "¥"}.get(c.closed.currency, "")
                    notify(
                        tr("{m} 的账已结好", m=c.month),
                        tr(
                            "收入 {i}，支出 {e}。之后这个月的数字再变，会提醒你。",
                            i=f"{sign}{c.income / 100:,.0f}",
                            e=f"{sign}{c.expenses / 100:,.0f}",
                        ),
                    )
        except Exception:
            log.exception("Month close failed")
        # Backup still runs if market data is unavailable.
        from pf.ops.backup import chosen_dir

        with transaction() as conn:
            dest = chosen_dir(conn)  # iCloud Drive when chosen in Settings
        log.info("Backup: %s", run_backup(dest))
        try:
            from pf.ops.update import notify_available

            log.info("Update check: %s", notify_available())
        except Exception:
            log.exception("Update check failed")
        try:
            from pf.ops.alerts import notify_new_alerts

            with transaction() as conn:
                log.info("Alerts sent: %s", notify_new_alerts(conn))
        except Exception:
            log.exception("Alerts failed")

    @staticmethod
    def monthly() -> None:
        from pf.db import transaction
        from pf.i18n import tr
        from pf.ops.monthly import previous_month, snapshot_month, summary_line
        from pf.ops.notify import notify
        from pf.prefs import use_saved_language

        month = previous_month()
        with transaction() as conn:
            use_saved_language(conn)
            report = snapshot_month(conn, month)
        line = summary_line(report)
        log.info("Monthly snapshot %s: %s", month, line)
        notify(tr("{month} 月报已生成", month=month), line)
