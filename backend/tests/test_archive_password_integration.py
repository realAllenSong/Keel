from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from pf.api.app import app
from pf.importers.archive_passwords import ArchivePasswords
from pf.ledger.importing import import_file

CN = Path(__file__).resolve().parents[2] / "fixtures" / "cn"
PASSWORD = "246810"


def test_saved_code_unlocks_new_archive_and_wrong_codes_remain_pending(conn, env):
    ArchivePasswords.remember("alipay", "wrong")
    report = import_file(conn, CN / "alipay_record_202609.zip")
    assert report.status == "needs_password"
    assert conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar() == 0
    ArchivePasswords.remember("alipay", PASSWORD)
    report = import_file(conn, CN / "alipay_record_202609.zip")
    assert report.status == "imported"
    assert conn.execute(text("SELECT COUNT(*) FROM pending_imports")).scalar() == 0
    assert import_file(conn, CN / "alipay_record_202609.zip").status == "already_imported"


def test_password_manager_retries_pending_and_never_returns_codes(env):
    with TestClient(app) as c:
        first = c.post(
            "/api/import",
            files=[("files", ("alipay.zip", (CN / "alipay_record_202609.zip").read_bytes()))],
        ).json()[0]
        assert first["status"] == "needs_password"
        wrong = c.put("/api/onboarding/statement-passwords/alipay", json={"password": "wrong"})
        assert wrong.status_code == 200
        assert wrong.json()["unlocked"] == 0 and wrong.json()["remaining"] == 1
        result = c.put("/api/onboarding/statement-passwords/alipay", json={"password": PASSWORD})
        assert result.status_code == 200, result.text
        assert result.json()["unlocked"] == 1 and result.json()["remaining"] == 0
        assert result.json()["new_transactions"] > 0
        for response in (result, c.get("/api/onboarding/statement-passwords")):
            assert PASSWORD not in response.text and "wrong" not in response.text
        assert c.delete("/api/onboarding/statement-passwords/alipay").status_code == 200
        assert not c.get("/api/onboarding/statement-passwords").json()[0]["configured"]
        assert (
            c.put(
                "/api/onboarding/statement-passwords/invalid", json={"password": PASSWORD}
            ).status_code
            == 422
        )
        assert (
            c.put(
                "/api/onboarding/statement-passwords/alipay", json={"password": " " * 140}
            ).status_code
            == 400
        )


def test_unlock_remember_is_opt_in_and_only_after_correct_code(env):
    with TestClient(app) as c:
        first = c.post(
            "/api/import",
            files=[("files", ("alipay.zip", (CN / "alipay_record_202609.zip").read_bytes()))],
        ).json()[0]
        endpoint = f"/api/onboarding/pending/{first['pending_sha']}/unlock"
        assert c.post(endpoint, json={"password": "wrong", "remember": True}).status_code == 400
        assert ArchivePasswords.candidates("alipay") == []
        assert c.post(endpoint, json={"password": PASSWORD, "remember": True}).status_code == 200
        assert ArchivePasswords.candidates("alipay") == [PASSWORD]


def test_older_saved_code_is_tried_after_newest_wrong_code(conn, env):
    ArchivePasswords.remember("alipay", PASSWORD)
    ArchivePasswords.remember("alipay", "new-but-wrong")
    assert import_file(conn, CN / "alipay_record_202609.zip").status == "imported"


def test_decrypted_unknown_format_replaces_password_step_with_format_step(conn, env, tmp_path):
    import pyzipper

    path = tmp_path / "bank.zip"
    with pyzipper.AESZipFile(
        path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as z:
        z.setpassword(b"012345")
        z.writestr("unknown-bank.csv", "Date,Amount,Description\n2026-09-01,-10,Test\n")
    first = import_file(conn, path)
    assert first.status == "needs_password"
    ArchivePasswords.remember("other", "012345")
    result = import_file(conn, path)
    assert result.status == "unknown_format"
    assert conn.execute(text("SELECT reason FROM pending_imports")).scalars().all() == [
        "unknown_format"
    ]


def test_malformed_password_requests_never_echo_supplied_codes(env):
    with TestClient(app) as c:
        first = c.post(
            "/api/import",
            files=[("files", ("alipay.zip", (CN / "alipay_record_202609.zip").read_bytes()))],
        ).json()[0]
        for endpoint in [
            "/api/onboarding/statement-passwords/alipay",
            f"/api/onboarding/pending/{first['pending_sha']}/unlock",
        ]:
            call = c.put if "statement-passwords" in endpoint else c.post
            for value in [246810, ["246810"], {"code": "246810"}]:
                r = call(endpoint, json={"password": value})
                assert r.status_code == 422
                assert "246810" not in r.text
            r = call(
                endpoint,
                content='{"password":246810 broken}',
                headers={"Content-Type": "application/json"},
            )
            assert r.status_code == 422 and "246810" not in r.text
