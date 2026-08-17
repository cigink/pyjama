"""Local SQL-assistant model lifecycle (Phase 10, P10.1-P10.3).

Downloads/verifies/activates the pinned SS-350M-SQL-Strict GGUF. Fails closed
on any checksum mismatch — a corrupted or tampered download never activates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

from .config import app_data_dir

MANIFEST = {
    "id": "slm-sql-0.5b-q8",
    "repo": "cycloneboy/SLM-SQL-0.5B",
    "filename": "SLM-SQL-0.5B.Q8_0.gguf",
    # No hosted GGUF exists for this checkpoint — cycloneboy publishes
    # safetensors only. We convert locally (transformers -> convert_hf_to_gguf.py
    # -> llama-quantize Q8_0) and activate via install_prebundled(), same as the
    # enterprise pre-bundled path (§7.3). install() (network download) is not
    # supported for this model until/unless a GGUF is hosted somewhere pinned.
    "download_url": "",
    "size_bytes": 675710976,
    "sha256": "6931fab89bf5d772ef8589d2e2b3588ea6fda501f4d81f24736acdec6033f234",
    "license": "CC-BY-NC-4.0",  # non-commercial — do not ship in a commercial build without re-checking
    "dialect": "standard-sql",
    "runtime": "llama.cpp",
    "base_model": "Qwen2.5-Coder-0.5B-Instruct",
}


class ModelError(Exception):
    pass


def models_dir() -> Path:
    d = app_data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_model_path() -> Path | None:
    """The model as shipped inside the app package — `sys._MEIPASS/models/`
    when frozen by PyInstaller (see pyjama.spec's `datas`), or the dev-mode
    `backend/vendor/models/` fallback. None if not bundled (e.g. this build
    was made without the vendor file present)."""
    import sys

    meipass = getattr(sys, "_MEIPASS", None)
    candidates = (
        [Path(meipass) / "models" / MANIFEST["filename"]] if meipass else []
    ) + [Path(__file__).resolve().parent.parent / "vendor" / "models" / MANIFEST["filename"]]
    for p in candidates:
        if p.exists():
            return p
    return None


def ensure_installed_from_bundle() -> ModelStatus:
    """First-run bootstrap (single-installer goal): if the model isn't in
    app-data yet but ships inside the package, copy it into place and verify
    the checksum. No-op if already installed or nothing is bundled — safe to
    call unconditionally on every startup."""
    if model_path().exists():
        return status()
    src = bundled_model_path()
    if src is None:
        return status()
    return install_prebundled(src)


def model_path() -> Path:
    return models_dir() / MANIFEST["filename"]


def state_path() -> Path:
    return models_dir() / "model_state.json"


@dataclass
class ModelStatus:
    installed: bool
    model_id: str
    filename: str
    size_bytes: int
    verified: bool
    smoke_test_passed: bool
    path: str | None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_state() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    state_path().write_text(json.dumps(state, indent=2))


def status() -> ModelStatus:
    p = model_path()
    installed = p.exists()
    state = _read_state()
    return ModelStatus(
        installed=installed,
        model_id=MANIFEST["id"],
        filename=MANIFEST["filename"],
        size_bytes=p.stat().st_size if installed else 0,
        verified=bool(state.get("verified")) and installed,
        smoke_test_passed=bool(state.get("smoke_test_passed")) and installed,
        path=str(p) if installed else None,
    )


def verify() -> bool:
    """Re-check the installed file's checksum against the pinned manifest."""
    p = model_path()
    if not p.exists():
        return False
    ok = _sha256(p) == MANIFEST["sha256"]
    state = _read_state()
    state["verified"] = ok
    _write_state(state)
    return ok


def install(progress=None) -> ModelStatus:
    """Download to a .partial file, verify sha256, atomic rename. Fails
    closed (deletes the partial, raises) on any mismatch (P10.1)."""
    if not MANIFEST["download_url"]:
        raise ModelError("no hosted download for this model; use install_prebundled() with a locally converted GGUF")
    dest = model_path()
    partial = dest.with_suffix(dest.suffix + ".partial")
    resp = requests.get(MANIFEST["download_url"], stream=True, timeout=60)
    resp.raise_for_status()
    written = 0
    with partial.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            written += len(chunk)
            if progress:
                progress(written, MANIFEST["size_bytes"])

    if written != MANIFEST["size_bytes"]:
        partial.unlink(missing_ok=True)
        raise ModelError(f"downloaded size {written} != expected {MANIFEST['size_bytes']}")
    digest = _sha256(partial)
    if digest != MANIFEST["sha256"]:
        partial.unlink(missing_ok=True)
        raise ModelError("checksum mismatch — download rejected")

    partial.rename(dest)
    _write_state({"verified": True, "smoke_test_passed": False})
    return status()


def install_prebundled(source: Path) -> ModelStatus:
    """Enterprise path (and the single-installer first-run bootstrap, P12): activate
    an admin-placed or app-bundled GGUF after verifying its checksum against
    the pinned manifest (P10.2). Streamed, not read fully into memory — the
    file is hundreds of MB."""
    digest = _sha256(source)
    if digest != MANIFEST["sha256"]:
        raise ModelError("pre-bundled model checksum does not match the pinned manifest")
    dest = model_path()
    import shutil as _shutil

    _shutil.copyfile(source, dest)
    _write_state({"verified": True, "smoke_test_passed": False})
    return status()


def mark_smoke_test_passed(passed: bool) -> None:
    state = _read_state()
    state["smoke_test_passed"] = passed
    _write_state(state)


def uninstall() -> None:
    model_path().unlink(missing_ok=True)
    state_path().unlink(missing_ok=True)
