import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from sqlalchemy import text

from pf.db import make_engine
from pf.ops.inbox import scan_watch_dirs
from tests.conftest import DEMO


def test_simultaneous_watcher_and_manual_scan_import_only_once(env: Path) -> None:
    downloads = env / "downloads"
    downloads.mkdir()
    source = DEMO / "Chase0002_Activity_202601.CSV"
    shutil.copy(source, downloads)
    barrier = Barrier(2)

    def scan():
        barrier.wait(timeout=5)
        return scan_watch_dirs(wait_settle=False)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(scan) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sum(len(result.imported) for result in results) == 1
    assert not any(result.failed for result in results)
    with make_engine().begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM import_files")).scalar() == 1
    assert (downloads / source.name).read_bytes() == source.read_bytes()
