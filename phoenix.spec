# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

BASE = Path(r"E:\EDIÇÃO\GRUPO FENIX\Phoenix-PhaseCancel-Windows\Phoenix-PhaseCancel")
FFMPEG = r"C:\Users\User\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"

a = Analysis(
    [str(BASE / "app_gui.py")],
    pathex=[str(BASE)],
    binaries=[
        (FFMPEG, "."),  # ffmpeg.exe na raiz do bundle
    ],
    datas=[
        (str(BASE / "scripts" / "ed"),             "scripts/ed"),
        (str(BASE / "scripts" / "diabetes"),        "scripts/diabetes"),
        (str(BASE / "scripts" / "emagrecimento"),   "scripts/emagrecimento"),
        (str(BASE / "scripts" / "neuropatia"),      "scripts/neuropatia"),
        (str(BASE / "scripts" / "memoria"),         "scripts/memoria"),
    ],
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
                   "customtkinter",
                   "requests", "requests.adapters", "requests.auth", "requests.compat",
                   "requests.cookies", "requests.exceptions", "requests.hooks",
                   "requests.models", "requests.packages", "requests.sessions",
                   "requests.structures", "requests.utils",
                   "urllib3", "urllib3.util", "urllib3.util.retry", "urllib3.util.ssl_",
                   "urllib3.contrib", "urllib3.packages",
                   "certifi", "charset_normalizer", "idna",
                   "tkinterdnd2"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PhoenixPhaseCancel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # sem janela preta
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(BASE / "phoenix.ico"),
)
