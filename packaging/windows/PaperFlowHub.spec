# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# SPECPATH points to packaging/windows. The repository root is two levels up.
ROOT = Path(SPECPATH).parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend" / "dist"

# Hook discovery runs before Analysis applies pathex. Without this explicit path,
# collect_submodules("app") returns an empty list and Uvicorn's dynamic
# "app.main:app" import fails only in the packaged windowed executable.
backend_path = str(BACKEND)
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

binaries = []
datas = []
hiddenimports = ["app", "app.main"]

for package in ("rapidocr_onnxruntime", "onnxruntime", "uvicorn", "fastapi", "sqlalchemy", "alembic", "pystray"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

hiddenimports += collect_submodules("app")
datas += collect_data_files("cv2")
datas += [
    (str(BACKEND / "alembic.ini"), "."),
    (str(BACKEND / "migrations"), "migrations"),
]
if FRONTEND.is_dir():
    datas.append((str(FRONTEND), "frontend/dist"))

analysis = Analysis(
    [str(BACKEND / "app" / "desktop.py")],
    pathex=[backend_path],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide6"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Chistovik",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Chistovik",
)
