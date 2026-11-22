"""App-home migration uses temporary homes, never the current user's files."""

from pathlib import Path

import pytest
from ledger_app import runtime


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PF_APP_HOME", raising=False)
    return tmp_path / "Library/Application Support"


def test_default_home_moves_existing_ledger_without_copying(isolated_home):
    old = isolated_home / "Ledger"
    old.mkdir(parents=True)
    database = old / "ledger.db"
    database.write_bytes(b"private ledger placeholder")
    inode = database.stat().st_ino

    home = runtime.app_home()

    assert home == isolated_home / "Keel"
    assert (home / "ledger.db").read_bytes() == b"private ledger placeholder"
    assert (home / "ledger.db").stat().st_ino == inode
    assert not old.exists()
    assert runtime.app_home() == home


def test_existing_keel_home_leaves_old_ledger_untouched(isolated_home):
    for name in ("Ledger", "Keel"):
        (isolated_home / name).mkdir(parents=True)
        (isolated_home / name / "marker").write_text(name)

    assert runtime.app_home() == isolated_home / "Keel"
    assert (isolated_home / "Ledger/marker").read_text() == "Ledger"
    assert (isolated_home / "Keel/marker").read_text() == "Keel"


def test_explicit_home_disables_migration(isolated_home, tmp_path, monkeypatch):
    old = isolated_home / "Ledger"
    old.mkdir(parents=True)
    custom = tmp_path / "custom"
    monkeypatch.setenv("PF_APP_HOME", str(custom))

    assert runtime.app_home() == custom
    assert old.is_dir()
    assert not (isolated_home / "Keel").exists()


def test_clean_install_uses_keel_without_creating_directories(isolated_home):
    assert runtime.app_home() == isolated_home / "Keel"
    assert not isolated_home.exists()


def test_migration_tolerates_another_launch_finishing_the_rename(isolated_home, monkeypatch):
    old = isolated_home / "Ledger"
    old.mkdir(parents=True)
    (old / "marker").write_text("keep me")
    rename = Path.rename

    def concurrent_rename(source, target):
        rename(source, target)
        raise FileNotFoundError(source)

    monkeypatch.setattr(Path, "rename", concurrent_rename)

    assert runtime.app_home() == isolated_home / "Keel"
    assert (isolated_home / "Keel/marker").read_text() == "keep me"


def test_migration_does_not_hide_a_missing_source_without_a_keel_home(isolated_home, monkeypatch):
    (isolated_home / "Ledger").mkdir(parents=True)

    def missing_source(source, target):
        raise FileNotFoundError(source)

    monkeypatch.setattr(Path, "rename", missing_source)
    with pytest.raises(FileNotFoundError):
        runtime.app_home()
