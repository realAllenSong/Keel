"""Nightly backups: consistent, verified, compressed snapshots + an append-only raw/ mirror.

* snapshot via SQLite's online backup API (safe while the app is writing)
* skipped when the ledger hasn't changed since the last snapshot
* zstd-compressed, then decompressed again and checked with PRAGMA integrity_check
* retention: newest per day (7 d), per ISO week (8 w), per month (12 m), per year (forever)
* raw/ statements mirrored append-only (never deleted from the backup)
The live ledger.db is never placed in a synced folder - only finished snapshots are."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

import zstandard
from sqlalchemy import Connection

from pf.settings import get_settings

ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
PREFIX = "ledger-"
SUFFIX = ".db.zst"


def backup_dir() -> Path:
    if env := os.environ.get("PF_BACKUP_DIR"):
        return Path(env)
    return (ICLOUD if ICLOUD.is_dir() else Path.home()) / "Finance-Backups"


def chosen_dir(conn: Connection) -> Path:
    """Where snapshots go: iCloud Drive when you turned that on in Settings (and it is there),
    else the app's own backup folder."""
    from pf.prefs import load_prefs

    if load_prefs(conn).backup_icloud and ICLOUD.is_dir():
        return ICLOUD / "Keel Backups"
    return backup_dir()


@dataclass
class BackupStatus:
    dir: str
    last: str | None  # ISO timestamp of the newest snapshot
    count: int
    icloud_available: bool


def status(dest: Path) -> BackupStatus:
    snaps = sorted(dest.glob(f"{PREFIX}*{SUFFIX}")) if dest.is_dir() else []
    last = _snapshot_ts(snaps[-1]).isoformat(timespec="minutes") if snaps else None
    return BackupStatus(str(dest), last, len(snaps), ICLOUD.is_dir())


@dataclass
class BackupResult:
    snapshot: Path | None  # None = unchanged, nothing written
    kept: int
    pruned: int
    raw_copied: int


def _snapshot_ts(p: Path) -> dt.datetime:
    return dt.datetime.strptime(p.name[len(PREFIX) : -len(SUFFIX)], "%Y%m%d-%H%M%S")


def select_keep(stamps: list[dt.datetime], now: dt.datetime) -> set[dt.datetime]:
    """Grandfather-father-son: newest snapshot in each bucket."""
    keep: set[dt.datetime] = set()
    buckets: dict[tuple[str, object], dt.datetime] = {}
    for s in sorted(stamps, reverse=True):  # newest first -> first seen wins
        age = now - s
        keys: list[tuple[str, object]] = [("year", s.year)]
        if age <= dt.timedelta(days=7):
            keys.append(("day", s.date()))
        if age <= dt.timedelta(weeks=8):
            keys.append(("week", tuple(s.isocalendar())[:2]))
        if age <= dt.timedelta(days=366):
            keys.append(("month", (s.year, s.month)))
        for k in keys:
            if k not in buckets:
                buckets[k] = s
                keep.add(s)
    return keep


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(snapshot: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        restored = Path(tmp) / "check.db"
        with snapshot.open("rb") as src, restored.open("wb") as dst:
            zstandard.ZstdDecompressor().copy_stream(src, dst)
        con = sqlite3.connect(restored)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            con.close()
        if ok != "ok":
            raise RuntimeError(f"backup verification failed: {ok}")


def mirror_raw(dest: Path) -> int:
    raw = get_settings().raw_dir
    if not raw.is_dir():
        return 0
    n = 0
    for f in raw.rglob("*"):
        if not f.is_file():
            continue
        target = dest / "raw" / f.relative_to(raw)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        n += 1
    return n


def run_backup(dest: Path | None = None, now: dt.datetime | None = None) -> BackupResult:
    dest = dest or backup_dir()
    now = now or dt.datetime.now()
    dest.mkdir(parents=True, exist_ok=True)
    db = get_settings().db_path
    snapshot: Path | None = None

    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "ledger.db"
        src = sqlite3.connect(db)
        out = sqlite3.connect(plain)
        try:
            src.backup(out)  # online backup API: consistent even during writes
        finally:
            out.close()
            src.close()
        digest = _sha256(plain)
        marker = dest / "latest.sha256"
        if not (marker.exists() and marker.read_text().strip() == digest):
            snapshot = dest / f"{PREFIX}{now:%Y%m%d-%H%M%S}{SUFFIX}"
            partial = snapshot.with_suffix(".partial")
            with plain.open("rb") as fi, partial.open("wb") as fo:
                zstandard.ZstdCompressor(level=10).copy_stream(fi, fo)
            _verify(partial)
            partial.rename(snapshot)  # atomic: a synced folder never sees half a file
            marker.write_text(digest + "\n")

    snaps = {_snapshot_ts(p): p for p in dest.glob(f"{PREFIX}*{SUFFIX}")}
    keep = select_keep(list(snaps), now)
    pruned = 0
    for ts, p in snaps.items():
        if ts not in keep:
            p.unlink()
            pruned += 1
    return BackupResult(snapshot, len(keep), pruned, mirror_raw(dest))


def restore(snapshot: Path, to: Path) -> Path:
    """Decompress a snapshot to `to` (refuses to overwrite an existing file)."""
    if to.exists():
        raise FileExistsError(f"{to} exists; restore to a new path and swap it in yourself")
    _verify(snapshot)
    with snapshot.open("rb") as src, to.open("wb") as dst:
        zstandard.ZstdDecompressor().copy_stream(src, dst)
    return to
