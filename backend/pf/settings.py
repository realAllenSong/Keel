"""Runtime paths and settings. Everything private lives under data/ and raw/ (gitignored)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = (
    Path(sys._MEIPASS) / "ledger_resources"  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[2]
)


def _env_path(var: str, default: Path) -> Path:
    return Path(os.environ.get(var, default))


@dataclass(frozen=True)
class Settings:
    root: Path = REPO_ROOT
    data_dir: Path = field(default_factory=lambda: _env_path("PF_DATA_DIR", REPO_ROOT / "data"))
    raw_dir: Path = field(default_factory=lambda: _env_path("PF_RAW_DIR", REPO_ROOT / "raw"))
    inbox_dir: Path = field(
        default_factory=lambda: _env_path("PF_INBOX_DIR", Path.home() / "Finance-Inbox")
    )
    config_dir: Path = field(
        default_factory=lambda: _env_path("PF_CONFIG_DIR", REPO_ROOT / "config")
    )

    @property
    def watch_dirs(self) -> list[Path]:
        configured = os.environ.get("PF_WATCH_DIRS")
        paths = (
            [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
            if configured is not None
            else [Path.home() / "Downloads", self.inbox_dir]
        )
        return list(dict.fromkeys(p.resolve() for p in paths))

    @property
    def db_path(self) -> Path:
        return _env_path("PF_DB", self.data_dir / "ledger.db")

    @property
    def market_dir(self) -> Path:
        return self.data_dir / "market"


def get_settings() -> Settings:
    return Settings()
