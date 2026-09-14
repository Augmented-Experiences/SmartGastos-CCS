# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec para empaquetar el backend FastAPI de SmartCaja como un
# binario único (sidecar de Tauri). Los recursos de solo lectura (app/, defaults/)
# se incluyen en el bundle; el backend los resuelve vía sys._MEIPASS cuando está
# congelado (ver server/app.py).
#
import json
from pathlib import Path

BACKEND_DIR = Path(SPECPATH).resolve()      # desktop/backend
DESKTOP = BACKEND_DIR.parent                 # desktop
ROOT = DESKTOP.parent                         # raíz del repo
SERVER = ROOT / "server"

# Config por-herramienta (excludes/datas opcionales)
_pyi = {}
try:
    with open(DESKTOP / "smartsuite.config.json", encoding="utf-8") as _f:
        _pyi = (json.load(_f) or {}).get("pyinstaller", {}) or {}
except Exception:
    _pyi = {}

datas = [
    (str(ROOT / "app"), "app"),
    (str(ROOT / "defaults"), "defaults"),
]
# Recursos adicionales por-herramienta: ["carpeta", ...] relativos a la raíz del repo
for _d in _pyi.get("extraDatas", []):
    _p = ROOT / _d
    if _p.exists():
        datas.append((str(_p), _d))

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

a = Analysis(
    [str(SERVER / "app.py")],
    pathex=[str(SERVER)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"] + list(_pyi.get("excludes", [])),
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
