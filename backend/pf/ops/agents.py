"""launchd agents: keep the app running and the schedules ticking without `make dev`.

  api      FastAPI on 127.0.0.1:8711                    (KeepAlive)
  watch    ~/Finance-Inbox watcher                       (KeepAlive)
  daily    07:30 market data + verified backup
  monthly  1st of month 08:00 snapshot + notification

Logs: data/logs/<agent>.log. Nothing is installed until you run `pf agents install`."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from pf.settings import REPO_ROOT, get_settings

PREFIX = "com.allensong.keel"
LEGACY_PREFIX = "com.personal-finance"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _env() -> dict[str, str]:
    path = os.pathsep.join(
        dict.fromkeys(
            [
                str(Path(shutil.which("uv") or "/opt/homebrew/bin/uv").parent),
                "/opt/homebrew/bin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
        )
    )
    s = get_settings()
    env = {"PATH": path, "PF_DB": str(s.db_path), "PF_RAW_DIR": str(s.raw_dir)}
    for k in ("PF_INBOX_DIR", "PF_BACKUP_DIR", "PF_CONFIG_DIR", "ANTHROPIC_API_KEY"):
        if v := os.environ.get(k):
            env[k] = v
    return env


def specs() -> dict[str, dict[str, object]]:
    backend = REPO_ROOT / "backend"
    pf = ["uv", "run", "--project", str(backend), "pf"]
    return {
        "api": {"args": [*pf, "serve"], "cwd": backend, "keepalive": True},
        "watch": {"args": [*pf, "watch"], "cwd": backend, "keepalive": True},
        "daily": {"args": [*pf, "daily"], "cwd": backend, "calendar": {"Hour": 7, "Minute": 30}},
        "monthly": {
            "args": [*pf, "monthly"],
            "cwd": backend,
            "calendar": {"Day": 1, "Hour": 8, "Minute": 0},
        },
    }


def plist_for(name: str, spec: dict[str, object]) -> bytes:
    logs = get_settings().data_dir / "logs"
    d: dict[str, object] = {
        "Label": f"{PREFIX}.{name}",
        "ProgramArguments": spec["args"],
        "WorkingDirectory": str(spec["cwd"]),
        "EnvironmentVariables": _env(),
        "StandardOutPath": str(logs / f"{name}.log"),
        "StandardErrorPath": str(logs / f"{name}.log"),
        "ProcessType": "Background",
    }
    if spec.get("keepalive"):
        d["KeepAlive"] = True
        d["RunAtLoad"] = True
        d["ThrottleInterval"] = 10
    if cal := spec.get("calendar"):
        d["StartCalendarInterval"] = cal
    return plistlib.dumps(d)


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def install(names: list[str] | None = None) -> list[str]:
    (get_settings().data_dir / "logs").mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    done = []
    for name, spec in specs().items():
        if names and name not in names:
            continue
        path = AGENTS_DIR / f"{PREFIX}.{name}.plist"
        _launchctl("bootout", f"gui/{os.getuid()}", str(path))
        path.write_bytes(plist_for(name, spec))
        r = _launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
        if r.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap {name}: {r.stderr.strip()}")
        # Keep the old job available if registering its replacement fails.
        legacy = AGENTS_DIR / f"{LEGACY_PREFIX}.{name}.plist"
        if legacy.exists():
            _launchctl("bootout", f"gui/{os.getuid()}", str(legacy))
            legacy.unlink()
        done.append(name)
    return done


def uninstall() -> list[str]:
    done = []
    for name in specs():
        removed = False
        for prefix in (PREFIX, LEGACY_PREFIX):
            path = AGENTS_DIR / f"{prefix}.{name}.plist"
            if path.exists():
                _launchctl("bootout", f"gui/{os.getuid()}", str(path))
                path.unlink()
                removed = True
        if removed:
            done.append(name)
    return done


def status() -> dict[str, str]:
    out = {}
    for name in specs():
        r = _launchctl("print", f"gui/{os.getuid()}/{PREFIX}.{name}")
        if r.returncode != 0:
            r = _launchctl("print", f"gui/{os.getuid()}/{LEGACY_PREFIX}.{name}")
        if r.returncode != 0:
            out[name] = "not installed"
            continue
        state = next(
            (
                ln.split("=", 1)[1].strip()
                for ln in r.stdout.splitlines()
                if ln.strip().startswith("state =")
            ),
            "?",
        )
        out[name] = state
    return out
