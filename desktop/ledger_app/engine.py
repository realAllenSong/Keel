"""Keel's engine: the Python ledger, API and background jobs, without any UI.

The SwiftUI app (apple/) starts this as a child process and talks to it over HTTP on
127.0.0.1. Protocol:
  - prints `KEEL_READY <url>` once the API accepts requests (migrations have run);
  - runs the inbox watcher and the daily/monthly jobs;
  - exits when its stdin closes, so it can never outlive the app (quit, crash or kill).

The same binary is Keel for agents: `keel-engine agent <command …>` is the `keel` CLI and
`keel-engine agent mcp` the MCP server (pf.agent), on the same ledger, with no server or jobs.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from ledger_app.jobs import Jobs
from ledger_app.runtime import LocalServer, configure

log = logging.getLogger("keel.engine")


def main() -> None:
    configure()
    if len(sys.argv) > 1 and sys.argv[1] == "agent":
        from pathlib import Path

        from pf.agent.cli import main as agent

        # Inside Keel.app the build's version file sits two folders up (Contents/Resources).
        info = Path(sys.executable).resolve().parents[2] / "build-info.json"
        if getattr(sys, "frozen", False) and info.is_file():
            os.environ["KEEL_BUILD_INFO"] = str(info)

        raise SystemExit(agent(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "background":
        raise SystemExit(background())
    if "--no-jobs" not in sys.argv and not os.environ.get("PF_APP_HOME"):
        keep_agent_current()  # a test or mock home must not rewrite the real agent
    server = LocalServer(0)
    jobs = Jobs()
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())

    def parent_watch() -> None:
        sys.stdin.buffer.read()  # returns at EOF: the app closed the pipe or died
        done.set()

    try:
        server.start()  # FastAPI lifespan migrates before accepting requests
        if "--no-jobs" not in sys.argv:
            jobs.start()
        print(f"KEEL_READY {server.url}", flush=True)
        threading.Thread(target=parent_watch, daemon=True, name="keel-parent").start()
        done.wait()
    except Exception:
        log.exception("Keel engine failed")
        raise
    finally:
        jobs.close()
        server.stop()


def background() -> int:
    """`keel-engine background`: what the LaunchAgent runs while the app is closed (sync, prices,
    backup; once a day after 7:30). Exits at once when nothing is due or the switch is off."""
    from pf.db import migrate, transaction
    from pf.prefs import load_prefs

    try:
        migrate()  # an update may have brought a newer engine than the ledger
        with transaction() as conn:
            if not load_prefs(conn).background_sync:
                return 0
        log.info("Background run: %s", Jobs().run_due() or "nothing due")
        return 0
    except Exception:
        log.exception("Background run failed")
        return 1


def keep_agent_current() -> None:
    """At every app launch: install, rewrite (Keel.app moved) or remove the LaunchAgent."""
    try:
        from pf.db import migrate, transaction
        from pf.ops.background import apply
        from pf.prefs import load_prefs

        migrate()
        with transaction() as conn:
            on = load_prefs(conn).background_sync
        log.info("Background sync agent: %s", apply(on))
    except Exception:
        log.exception("Background sync agent not updated")


if __name__ == "__main__":
    main()
