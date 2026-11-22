# -*- mode: python ; coding: utf-8 -*-
# The headless engine bundled inside the SwiftUI Keel.app (Contents/Resources/engine).
# No Qt: PyInstaller injects Analysis/PYZ/EXE/COLLECT and SPECPATH.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

desktop = Path(SPECPATH)
root = desktop.parent
resources = "ledger_resources"
datas = [
    (str(root / "backend/alembic.ini"), f"{resources}/backend"),
    (str(root / "backend/migrations"), f"{resources}/backend/migrations"),
]
datas += [(str(p), f"{resources}/config") for p in (root / "config").glob("*.example.yaml")]
datas += collect_data_files("pf", includes=["**/*.yaml", "**/*.csv"])  # institutions, seed rules, FX baseline
datas += copy_metadata("yfinance")
datas += copy_metadata("mcp")  # `keel mcp` (pf.agent.mcp_server)

a = Analysis(
    [str(desktop / "ledger_app/engine.py")],
    pathex=[str(desktop), str(root / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("pf") + collect_submodules("uvicorn") + collect_submodules("mcp") + [
        "alembic", "sqlalchemy.dialects.sqlite", "watchdog.observers.kqueue",
    ],
    excludes=["PySide6", "PyQt5", "PyQt6", "PySide2", "tkinter", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="keel-engine", console=True,
    strip=False, upx=False, codesign_identity=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="keel-engine")
