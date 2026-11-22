"""Paths, logging and loopback server. This module does not import pf or Qt."""

from __future__ import annotations

import logging
import os
import shutil
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "ledger_resources"
    return Path(__file__).resolve().parents[2]


def app_home() -> Path:
    """Adopt the old default home once; explicit overrides never migrate anything."""
    if override := os.environ.get("PF_APP_HOME"):
        return Path(override)
    support = Path.home() / "Library/Application Support"
    home, legacy = support / "Keel", support / "Ledger"
    if not home.exists() and legacy.is_dir():
        try:
            legacy.rename(home)
        except FileNotFoundError:
            # Another first launch may have completed the same in-place migration.
            if not home.is_dir():
                raise
    return home


def configure() -> Path:
    home = app_home()
    defaults = {
        "PF_DATA_DIR": home / "data",
        "PF_RAW_DIR": home / "raw",
        "PF_CONFIG_DIR": home / "config",
        "PF_INBOX_DIR": home / "Inbox",
        "PF_BACKUP_DIR": home / "backups",
    }
    for key, default in defaults.items():
        os.environ.setdefault(key, str(default))
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ.setdefault("PF_DB", str(Path(os.environ["PF_DATA_DIR"]) / "ledger.db"))
    os.environ.setdefault(
        "PF_WATCH_DIRS",
        os.pathsep.join((str(Path.home() / "Downloads"), os.environ["PF_INBOX_DIR"])),
    )
    # Self-update (pf.ops.update): what this build is and where its bundle lives.
    os.environ.setdefault("KEEL_BUILD_INFO", str(resource_root() / "build-info.json"))
    os.environ.setdefault("KEEL_APP_PID", str(os.getpid()))
    if getattr(sys, "frozen", False):
        # .../Keel.app/Contents/MacOS/Keel -> .../Keel.app
        os.environ.setdefault("KEEL_APP_BUNDLE", str(Path(sys.executable).resolve().parents[2]))
    config = Path(os.environ["PF_CONFIG_DIR"])
    for source in (resource_root() / "config").glob("*.example.yaml"):
        dest = config / source.name
        if not dest.exists():
            shutil.copyfile(source, dest)
    # Synthetic example bindings must not silently claim real downloaded statements.
    accounts = config / "accounts.local.yaml"
    if not accounts.exists():
        with accounts.open("x") as stream:
            stream.write("# Accounts are added through onboarding.\naccounts: []\n")
    logs = Path(os.environ.get("PF_LOG_DIR", Path.home() / "Library/Logs/Keel"))
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = RotatingFileHandler(logs / "keel.log", maxBytes=5_000_000, backupCount=3)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    # Windowed PyInstaller processes may have no stdout/stderr; libraries still print.
    if sys.stdout is None:
        sys.stdout = (logs / "console.log").open("a", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    return home


class LocalServer:
    def __init__(self, port: int = 0) -> None:
        import uvicorn

        from pf.api.app import app

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", port))
        self.socket.listen(128)
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.server = uvicorn.Server(
            uvicorn.Config(app, log_config=None, access_log=False, loop="asyncio")
        )
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [self.socket]},
            daemon=True,
            name="keel-api",
        )

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 60
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("Keel API startup failed; see keel.log")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.socket.close()
