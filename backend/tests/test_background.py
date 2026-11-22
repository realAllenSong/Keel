"""The daily LaunchAgent (sync while Keel is closed): written to a temp folder, launchctl faked."""

from __future__ import annotations

import plistlib

import pytest

from pf.ops import background


@pytest.fixture
def agents(tmp_path, monkeypatch):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setenv("KEEL_LAUNCH_AGENTS", str(tmp_path / "LaunchAgents"))
    monkeypatch.setenv(
        "KEEL_ENGINE_BIN",
        "/Applications/Keel.app/Contents/Resources/engine/keel-engine/keel-engine",
    )
    monkeypatch.setattr(background, "_launchctl", lambda *a: calls.append(a))
    return calls


def test_on_installs_once_and_runs_the_engine_daily(agents):
    assert background.apply(True) == "on"
    plist = plistlib.loads(background.plist_path().read_bytes())
    assert plist["ProgramArguments"][1:] == ["background"]
    assert plist["ProgramArguments"][0].endswith("keel-engine")
    assert plist["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert plist["RunAtLoad"] is True
    assert [c[0] for c in agents] == ["bootstrap"]
    assert background.apply(True) == "on"  # unchanged: launchd is left alone
    assert [c[0] for c in agents] == ["bootstrap"]
    assert background.state() == "on"


def test_moved_app_rewrites_the_agent(agents, monkeypatch):
    background.apply(True)
    monkeypatch.setenv(
        "KEEL_ENGINE_BIN",
        "/Users/x/Applications/Keel.app/Contents/Resources/engine/keel-engine/keel-engine",
    )
    background.apply(True)
    plist = plistlib.loads(background.plist_path().read_bytes())
    assert plist["ProgramArguments"][0].startswith("/Users/x/")
    assert [c[0] for c in agents] == ["bootstrap", "bootout", "bootstrap"]


def test_off_removes_it(agents):
    background.apply(True)
    assert background.apply(False) == "off"
    assert not background.plist_path().exists()
    assert agents[-1][0] == "bootout"
    assert background.state() == "off"


def test_source_checkout_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_LAUNCH_AGENTS", str(tmp_path))
    monkeypatch.delenv("KEEL_ENGINE_BIN", raising=False)
    monkeypatch.setattr(background, "_launchctl", lambda *a: pytest.fail("launchctl called"))
    assert background.apply(True) == "unavailable"
    assert background.apply(False) == "unavailable"
    assert list(tmp_path.iterdir()) == []


def test_settings_switch_drives_the_agent(env, agents):
    from fastapi.testclient import TestClient

    from pf.api.app import app

    with TestClient(app) as client:
        _switch(client)


def _switch(client) -> None:
    prefs = client.get("/api/settings").json()
    assert prefs["background_sync"] is True  # on by default
    client.put("/api/settings", json={**prefs, "background_sync": False})
    assert not background.plist_path().exists()
    client.put("/api/settings", json={**prefs, "background_sync": True})
    assert background.plist_path().exists()
