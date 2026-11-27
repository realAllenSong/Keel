"""Statement password storage against a temporary file, never the real Keychain."""

from __future__ import annotations

import datetime as dt
import json
import stat
from pathlib import Path

import pytest

from pf.connectors.base import Secrets
from pf.importers.archive_passwords import ArchivePasswords, provider_for_member


@pytest.fixture(autouse=True)
def secrets_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("PF_SECRETS_FILE", str(path))
    return path


def test_status_lists_unconfigured_providers_without_creating_storage(secrets_file: Path) -> None:
    assert ArchivePasswords.status() == [
        {"provider": "alipay", "configured": False, "count": 0, "updated_at": None},
        {"provider": "wechat", "configured": False, "count": 0, "updated_at": None},
        {"provider": "cmb", "configured": False, "count": 0, "updated_at": None},
        {"provider": "ningbo", "configured": False, "count": 0, "updated_at": None},
        {"provider": "boc", "configured": False, "count": 0, "updated_at": None},
        {"provider": "other", "configured": False, "count": 0, "updated_at": None},
    ]
    assert ArchivePasswords.candidates("alipay") == []
    assert not secrets_file.exists()


def test_remember_preserves_zeroes_trims_and_promotes_duplicates() -> None:
    ArchivePasswords.remember("alipay", "  001234\n")
    ArchivePasswords.remember("alipay", "009876")
    ArchivePasswords.remember("alipay", "\t001234 ")

    assert ArchivePasswords.candidates("alipay") == ["001234", "009876"]


def test_only_five_most_recent_distinct_passwords_are_retained() -> None:
    for password in ["000001", "000002", "000003", "000004", "000005", "000006"]:
        ArchivePasswords.remember("wechat", password)
    ArchivePasswords.remember("wechat", "000003")

    assert ArchivePasswords.candidates("wechat") == [
        "000003",
        "000006",
        "000005",
        "000004",
        "000002",
    ]
    assert ArchivePasswords.status()[1]["count"] == 5


def test_passwords_are_separate_from_each_other_and_connector_credentials(
    secrets_file: Path,
) -> None:
    Secrets.set("mailbox", {"password": "mailbox-fixture"})
    ArchivePasswords.remember("alipay", "alipay-fixture")
    ArchivePasswords.remember("wechat", "wechat-fixture")
    ArchivePasswords.remember("other", "other-fixture")
    ArchivePasswords.remember("cmb", "cmb-fixture")
    ArchivePasswords.remember("ningbo", "ningbo-fixture")

    assert ArchivePasswords.candidates("alipay") == ["alipay-fixture"]
    assert ArchivePasswords.candidates("wechat") == ["wechat-fixture"]
    assert ArchivePasswords.candidates("other") == ["other-fixture"]
    assert ArchivePasswords.candidates("cmb") == ["cmb-fixture"]
    assert ArchivePasswords.candidates("ningbo") == ["ningbo-fixture"]
    assert set(json.loads(secrets_file.read_text())) == {
        "mailbox",
        "statement-passwords:alipay",
        "statement-passwords:wechat",
        "statement-passwords:other",
        "statement-passwords:cmb",
        "statement-passwords:ningbo",
    }
    assert Secrets.get("mailbox") == {"password": "mailbox-fixture"}


def test_clear_removes_only_selected_provider_and_is_idempotent(secrets_file: Path) -> None:
    ArchivePasswords.clear("other")
    assert not secrets_file.exists()
    ArchivePasswords.remember("alipay", "alipay-fixture")
    ArchivePasswords.remember("wechat", "wechat-fixture")

    ArchivePasswords.clear("alipay")
    ArchivePasswords.clear("alipay")

    assert ArchivePasswords.candidates("alipay") == []
    assert ArchivePasswords.candidates("wechat") == ["wechat-fixture"]
    assert ArchivePasswords.status()[0] == {
        "provider": "alipay",
        "configured": False,
        "count": 0,
        "updated_at": None,
    }
    assert "statement-passwords:alipay" not in json.loads(secrets_file.read_text())


def test_status_exposes_only_metadata_and_a_utc_timestamp(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level("DEBUG")
    before = dt.datetime.now(dt.UTC)
    ArchivePasswords.remember("alipay", "private-statement-fixture")
    status = ArchivePasswords.status()
    after = dt.datetime.now(dt.UTC)

    assert status[0]["configured"] is True
    assert status[0]["count"] == 1
    assert set(status[0]) == {"provider", "configured", "count", "updated_at"}
    updated = dt.datetime.fromisoformat(status[0]["updated_at"])
    assert updated.utcoffset() == dt.timedelta(0)
    assert before <= updated <= after
    exposed = repr(status) + repr(ArchivePasswords()) + repr(ArchivePasswords) + caplog.text
    captured = capsys.readouterr()
    exposed += captured.out + captured.err
    assert "private-statement-fixture" not in exposed


@pytest.mark.parametrize("password", ["", " ", "\n\t", "x" * 129, None, 123456])
def test_invalid_passwords_are_rejected_without_persistence(
    password: object, secrets_file: Path
) -> None:
    with pytest.raises(ValueError) as error:
        ArchivePasswords.remember("alipay", password)  # type: ignore[arg-type]

    if isinstance(password, str) and password.strip():
        assert password not in str(error.value)
    assert ArchivePasswords.candidates("alipay") == []
    assert not secrets_file.exists()


def test_maximum_length_applies_after_whitespace_is_stripped() -> None:
    ArchivePasswords.remember("other", "  " + "x" * 128 + "\n")

    assert ArchivePasswords.candidates("other") == ["x" * 128]


@pytest.mark.parametrize("provider", ["", "Alipay", "支付宝", "mailbox", None])
def test_unknown_providers_are_rejected_without_changing_storage(
    provider: object, secrets_file: Path
) -> None:
    ArchivePasswords.remember("other", "saved-fixture")
    original = secrets_file.read_text()

    for operation in (
        lambda: ArchivePasswords.remember(provider, "new-fixture"),
        lambda: ArchivePasswords.candidates(provider),
        lambda: ArchivePasswords.clear(provider),
    ):
        with pytest.raises(ValueError):
            operation()

    assert secrets_file.read_text() == original


def test_fake_secret_file_remains_private_after_save_and_clear(secrets_file: Path) -> None:
    ArchivePasswords.remember("other", "temporary-fixture")
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600

    ArchivePasswords.clear("other")

    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
    assert json.loads(secrets_file.read_text()) == {}


@pytest.mark.parametrize(
    ("member", "provider"),
    [
        ("支付宝交易明细.csv", "alipay"),
        ("statements/ALIPAY/transactions.xlsx", "alipay"),
        ("微信支付账单.csv", "wechat"),
        ("folder/WeChat-payments.csv", "wechat"),
        ("招商银行交易流水.pdf", "cmb"),
        ("宁波银行账户明细.xlsx", "ningbo"),
        ("bank-statement.csv", "other"),
        ("", "other"),
    ],
)
def test_member_names_identify_wallet_provider(member: str, provider: str) -> None:
    assert provider_for_member(member) == provider
