"""Self-update from the app's git clone: check against a bare 'origin', then the full apply flow
(pull, staged build, quit, swap, relaunch) with stand-ins for make/osascript/open."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pf.ops import update


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repos(tmp_path: Path, env: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """origin (bare) + the user's clone, built at the clone's HEAD, plus a running 'Keel.app'."""
    origin, clone, dev = tmp_path / "origin.git", tmp_path / "clone", tmp_path / "dev"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True)
    for repo in (dev,):
        git(repo, "config", "user.email", "dev@example.com")
        git(repo, "config", "user.name", "dev")
    (dev / "README").write_text("v1\n")
    git(dev, "add", ".")
    git(dev, "commit", "-qm", "v1")
    git(dev, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    info = tmp_path / "build-info.json"
    info.write_text(
        json.dumps(
            {"commit": git(clone, "rev-parse", "HEAD"), "branch": "main", "repo": str(clone)}
        )
    )
    app = tmp_path / "Applications" / "Keel.app"
    app.mkdir(parents=True)
    (app / "version").write_text("old\n")
    monkeypatch.setenv("KEEL_BUILD_INFO", str(info))
    monkeypatch.setenv("KEEL_APP_BUNDLE", str(app))
    monkeypatch.setenv("KEEL_APP_PID", "999999")  # not running: the script need not wait
    monkeypatch.setenv("PF_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("KEEL_UPDATE_QUIT_CMD", "true")
    monkeypatch.setenv("KEEL_UPDATE_OPEN_CMD", 'echo opened > "$STAGE/opened"')
    monkeypatch.setenv(
        "KEEL_UPDATE_BUILD_CMD",
        'mkdir -p "$STAGE/Keel.app" && git rev-parse --short HEAD > "$STAGE/Keel.app/version"',
    )
    return dev, clone


def push_change(dev: Path, text: str) -> None:
    (dev / "README").write_text(text)
    git(dev, "commit", "-qam", f"change: {text.strip()}")
    git(dev, "push", "-q", "origin", "main")


def test_up_to_date_then_behind_with_change_list(repos: tuple[Path, Path]) -> None:
    dev, _ = repos
    assert update.check().reason == "up_to_date"
    push_change(dev, "v2\n")
    push_change(dev, "v3\n")
    r = update.check()
    assert r.behind == 2 and r.can_apply and r.reason is None
    assert [c.subject for c in r.changes] == ["change: v3", "change: v2"]


def test_local_changes_or_dev_mode_block_apply(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    dev, clone = repos
    push_change(dev, "v2\n")
    (clone / "README").write_text("my edit\n")
    assert update.check().reason == "local_changes"
    git(clone, "checkout", "-q", "README")
    monkeypatch.delenv("KEEL_APP_BUNDLE")  # make dev: nothing to replace
    assert update.check().reason == "source_mode"
    with pytest.raises(ValueError, match="source_mode"):
        update.apply()


def test_apply_pulls_builds_swaps_and_relaunches(repos: tuple[Path, Path]) -> None:
    dev, clone = repos
    push_change(dev, "v2\n")
    app = Path(update.build_info().bundle or "")
    result = update.apply(wait=True)
    assert result["state"] == "done", result
    new = git(clone, "rev-parse", "--short", "HEAD")
    assert result["target"] == new
    assert (app / "version").read_text().strip() == new  # the new build is in place
    stage = clone / "dist" / "update-staging"
    assert (stage / "Keel.previous.app" / "version").read_text() == "old\n"  # rollback kept
    assert (stage / "opened").exists()
    # A backup of the ledger was taken first.
    from pf.ops.backup import backup_dir

    assert list(backup_dir().glob("**/*.db*"))


def test_failed_build_leaves_the_running_app_alone(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    dev, _ = repos
    push_change(dev, "v2\n")
    monkeypatch.setenv("KEEL_UPDATE_BUILD_CMD", "echo 'pnpm: command not found' >&2; exit 1")
    app = Path(update.build_info().bundle or "")
    result = update.apply(wait=True)
    assert result["state"] == "failed" and result["step"] == "build"
    assert "pnpm: command not found" in result["log"]
    assert (app / "version").read_text() == "old\n"


def test_api(repos: tuple[Path, Path]) -> None:
    from fastapi.testclient import TestClient

    from pf.api.app import app

    dev, _ = repos
    push_change(dev, "v2\n")
    with TestClient(app) as c:
        v = c.get("/api/app/version").json()
        assert v["branch"] == "main" and not v["source_mode"]
        check = c.post("/api/app/update/check").json()
        assert check["behind"] == 1 and check["can_apply"]
