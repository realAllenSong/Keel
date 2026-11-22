"""Keel's daily job while the app is closed: a per-user LaunchAgent that runs the engine's
`background` command (sync banks and brokers, prices, backup) at 7:30 and at login.

The job itself decides whether anything is due (once a day, after 7:30) and takes a lock, so the
open app and the agent never run the daily work twice. Settings › 启动 turns it on and off
(prefs.background_sync, on by default); every app launch rewrites the agent if Keel.app moved.

Only a packaged engine (Keel.app) installs it: a source checkout has no stable binary to point
launchd at, so there it reports "unavailable" and touches nothing.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Literal

LABEL = "com.allensong.keel.daily"
State = Literal["on", "off", "unavailable"]


def agents_dir() -> Path:
    return Path(os.environ.get("KEEL_LAUNCH_AGENTS") or Path.home() / "Library/LaunchAgents")


def plist_path() -> Path:
    return agents_dir() / f"{LABEL}.plist"


def engine_path() -> Path | None:
    """The keel-engine binary inside Keel.app (KEEL_ENGINE_BIN overrides, for tests)."""
    if override := os.environ.get("KEEL_ENGINE_BIN"):
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def agent_plist(engine: Path) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": [str(engine), "background"],
        # launchd runs a missed calendar slot when the Mac wakes; RunAtLoad covers a Mac that
        # was off at 7:30 (it runs at login). The job skips itself when today's run is done.
        "StartCalendarInterval": {"Hour": 7, "Minute": 30},
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",  # the engine logs to ~/Library/Logs/Keel/keel.log
    }


def _launchctl(*args: str) -> None:
    if os.environ.get("KEEL_LAUNCHCTL") == "0":
        return
    subprocess.run(["launchctl", *args], capture_output=True, check=False, timeout=15)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def apply(enabled: bool) -> State:
    """Make the LaunchAgent match the setting. Idempotent; cheap when nothing changed."""
    engine = engine_path()
    if engine is None:
        return "unavailable"
    path = plist_path()
    if not enabled:
        if path.exists():
            _launchctl("bootout", f"{_domain()}/{LABEL}")
            path.unlink()
        return "off"
    want = plistlib.dumps(agent_plist(engine))
    if path.exists():
        if path.read_bytes() == want:
            return "on"
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".tmp")
    partial.write_bytes(want)
    partial.replace(path)
    _launchctl("bootstrap", _domain(), str(path))
    return "on"


def state() -> State:
    if engine_path() is None:
        return "unavailable"
    return "on" if plist_path().exists() else "off"
