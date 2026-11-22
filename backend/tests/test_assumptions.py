"""Planning assumptions: complete defaults with no file; Settings saves overrides the app owns."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pf.api.app import app
from pf.reports.common import load_assumptions


@pytest.fixture
def own_config(env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config folder of this test's own (empty: a fresh install has no assumptions file)."""
    d = env / "config"
    d.mkdir()
    monkeypatch.setenv("PF_CONFIG_DIR", str(d))
    return d


def test_defaults_without_any_file_are_complete(own_config: Path) -> None:
    a = load_assumptions()
    assert a.base_currency == "USD" and a.expected("brokerage") == 0.08
    assert a.sim_mapping["term_deposit"] == {"bonds": 1.0}


def test_settings_save_override_and_reset(own_config: Path) -> None:
    with TestClient(app) as c:
        got = c.get("/api/assumptions").json()
        assert got["currencies"] == ["USD", "CNY"]
        body = got["current"] | {"base_currency": "CNY", "withdrawal_rate": 0.035}
        body["expected_return"] = body["expected_return"] | {"brokerage": 0.06}
        saved = c.put("/api/assumptions", json=body).json()["current"]
        assert saved["base_currency"] == "CNY" and saved["expected_return"]["brokerage"] == 0.06
        a = load_assumptions()
        assert a.base_currency == "CNY" and a.withdrawal_rate == 0.035
        assert a.expected("cash") == 0.04 and a.sim_mapping["brokerage"] == {"stocks": 1.0}
        bad = body | {"withdrawal_rate": 0.5}
        assert c.put("/api/assumptions", json=bad).status_code == 422
        assert (own_config / "assumptions.local.yaml").exists()
        assert c.delete("/api/assumptions").json()["current"]["base_currency"] == "USD"
        assert load_assumptions().base_currency == "USD"
