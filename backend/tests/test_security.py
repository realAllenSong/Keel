from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pf.api.app import app


@pytest.fixture
def client(env: Path):  # type: ignore[no-untyped-def]
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("host", ["127.0.0.1:8711", "localhost:8710", "[::1]:8711"])
def test_loopback_hosts_allowed(client: TestClient, host: str) -> None:
    assert client.get("/api/health", headers={"host": host}).status_code == 200


def test_dns_rebinding_host_rejected(client: TestClient) -> None:
    r = client.get("/api/health", headers={"host": "evil.example:8711"})
    assert r.status_code == 403 and "host" in r.json()["detail"]


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://192.168.1.5:8710"])
def test_cross_site_writes_rejected(client: TestClient, origin: str) -> None:
    r = client.post("/api/inbox/scan", headers={"origin": origin})
    assert r.status_code == 403


@pytest.mark.parametrize("origin", [None, "http://localhost:8710", "http://127.0.0.1:56806"])
def test_same_machine_writes_allowed(client: TestClient, origin: str | None) -> None:
    headers = {"origin": origin} if origin else {}
    assert client.post("/api/inbox/scan", headers=headers).status_code == 200


def test_cross_site_reads_still_need_cors(client: TestClient) -> None:
    """GETs aren't blocked by Origin (browsers can't read them without CORS), but host still is."""
    r = client.get("/api/health", headers={"origin": "https://evil.example"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
