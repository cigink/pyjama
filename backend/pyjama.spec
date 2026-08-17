# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: bundle the Python backend + built React UI + pywebview into a
# single standalone desktop app. macOS -> PyJama.app bundle; Windows -> onedir
# folder with PyJama.exe. Same spec file, platform branches below.

import os
import sys
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

# Pull in packages that PyInstaller can't fully trace on its own.
for pkg in ("pyarrow", "duckdb", "webview", "keyring", "uvicorn", "fastapi", "pydantic", "anyio", "openpyxl", "multipart", "cryptography", "sqlglot"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# uvicorn / keyring backends loaded dynamically.
hiddenimports += [
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
]

# Platform-specific hidden imports — importing the wrong OS's module here
# breaks PyInstaller's analysis pass (e.g. webview.platforms.cocoa imports
# pyobjc, which doesn't exist on Windows).
if sys.platform == "darwin":
    hiddenimports += ["webview.platforms.cocoa", "keyring.backends.macOS"]
elif sys.platform == "win32":
    hiddenimports += ["webview.platforms.edgechromium", "webview.platforms.winforms", "keyring.backends.Windows"]

# Bundle the built React UI as `ui/` (server.py resolves it via sys._MEIPASS).
ui_dist = os.path.join(SPECPATH, "..", "frontend", "dist")
datas += [(ui_dist, "ui")]

# Single-installer goal: bundle the local AI model + its llama.cpp sidecar
# binary directly into the app, so first launch is fully self-contained —
# no separate download, no Homebrew/system llama.cpp dependency. See
# ai_model.ensure_installed_from_bundle() (copies the model into app-data on
# first run) and ai_runtime._find_binary() (prefers this bundled sidecar,
# pointed at the vendored CPU-only backend via GGML_BACKEND_PATH — verified
# portable with zero external dependencies).
vendor_models = os.path.join(SPECPATH, "vendor", "models")
if os.path.isdir(vendor_models):
    datas += [(vendor_models, "models")]

vendor_llama_dir = "llama-windows" if sys.platform == "win32" else "llama-macos"
vendor_llama = os.path.join(SPECPATH, "vendor", vendor_llama_dir)
if os.path.isdir(vendor_llama):
    for fname in os.listdir(vendor_llama):
        binaries += [(os.path.join(vendor_llama, fname), "llama")]

block_cipher = None

a = Analysis(
    ["run_app.py"],
    pathex=[SPECPATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PyJama",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PyJama",
)

# BUNDLE (.app) is a macOS-only concept. On Windows, `coll` above (the
# onedir COLLECT output containing PyJama.exe) is the finished artifact —
# nothing further to wrap.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PyJama.app",
        icon=None,
        bundle_identifier="com.pyjama.workspace",
        info_plist={
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.developer-tools",
        },
    )
