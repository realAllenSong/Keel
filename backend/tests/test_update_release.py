"""The public app updates from GitHub Releases, and only to a build signed by Keel's team."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pf.ops import update


def _release(
    version: str, asset: bool = True, url: str = "https://example.invalid/Keel-macOS.zip"
) -> dict:
    return {
        "tag_name": f"v{version}",
        "published_at": "2026-11-20T10:00:00Z",
        "body": "- 更快的规划\n- 修复对账",
        "assets": [{"name": "Keel-macOS.zip", "browser_download_url": url}] if asset else [],
    }


@pytest.fixture
def release_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    info = tmp_path / "build-info.json"
    info.write_text(json.dumps({"version": "1.0.0", "channel": "release"}))
    app = tmp_path / "Applications" / "Keel.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "marker").write_text("old")
    monkeypatch.setenv("KEEL_BUILD_INFO", str(info))
    monkeypatch.setenv("KEEL_APP_BUNDLE", str(app))
    return app


def test_release_check_compares_versions(
    env: Path, release_build: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "_latest_release", lambda: _release("1.2.0"))
    c = update.check()
    assert (c.current, c.latest, c.behind, c.can_apply) == ("1.0.0", "1.2.0", 1, True)
    assert [x.subject for x in c.changes] == ["更快的规划", "修复对账"]
    monkeypatch.setattr(update, "_latest_release", lambda: _release("1.0.0"))
    assert update.check().reason == "up_to_date"
    monkeypatch.setattr(update, "_latest_release", lambda: _release("1.10.0", asset=False))
    assert update.check().reason == "no_asset"  # and 1.10.0 > 1.9.x compares numerically

    def offline() -> dict:
        raise OSError("network is unreachable")

    monkeypatch.setattr(update, "_latest_release", offline)
    assert "unreachable" in (update.check().error or "")


def test_release_update_refuses_an_unsigned_build(
    env: Path, release_build: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake" / "Keel.app" / "Contents"
    fake.mkdir(parents=True)
    (fake / "marker").write_text("new")
    zipped = tmp_path / "Keel-macOS.zip"
    subprocess.run(["ditto", "-c", "-k", "--keepParent", str(fake.parent), str(zipped)], check=True)
    monkeypatch.setattr(update, "_latest_release", lambda: _release("2.0.0", url=zipped.as_uri()))
    monkeypatch.setattr("pf.ops.backup.run_backup", lambda *a, **k: None)
    monkeypatch.setenv("KEEL_UPDATE_QUIT_CMD", "true")
    monkeypatch.setenv("KEEL_UPDATE_OPEN_CMD", "true")
    monkeypatch.setenv("KEEL_APP_PID", "999999")
    update.apply(wait=True)
    st = update.status()
    assert st["state"] == "failed" and st["step"] == "verify"
    assert (release_build / "Contents" / "marker").read_text() == "old"  # untouched
