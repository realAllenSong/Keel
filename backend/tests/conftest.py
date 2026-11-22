from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection

from pf.db import make_engine, migrate

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "fixtures" / "demo" / "statements"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Every path a test can write to points inside tmp_path - never at the real data/.
    monkeypatch.setenv("PF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PF_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("PF_INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("PF_NOTIFY", "0")
    monkeypatch.setenv("PF_SECRETS_FILE", str(tmp_path / "secrets.json"))
    monkeypatch.setenv(
        "PF_WATCH_DIRS", os.pathsep.join(str(tmp_path / name) for name in ("downloads", "inbox"))
    )
    monkeypatch.setenv("PF_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("PF_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("PF_CONFIG_DIR", str(REPO / "config"))
    migrate(make_engine())
    return tmp_path


@pytest.fixture
def conn(env: Path) -> Iterator[Connection]:
    with make_engine().begin() as c:
        yield c


@pytest.fixture
def no_fx_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only downloaded rates: for tests of how a missing rate is reported (any currency
    without the bundled baseline takes this path)."""
    monkeypatch.setattr("pf.reports.common._baseline_rate", lambda *_: None)
