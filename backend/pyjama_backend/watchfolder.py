"""Watched folder (Phase 5 — Epic X, minimal).

Scans a designated folder for supported files and reports which have finished
writing (stability check), so they can be imported as local join sources. The
first implementation is pull-based (scan on request); an OS file-watcher and
auto-run recipes are later work (PRD §14).
"""

from __future__ import annotations

import time
from pathlib import Path

from . import localsource
from .keystore import KeyStore
from .sources import SourceManifest

STABLE_SECONDS = 1.0


def scan(folder: str) -> list[dict]:
    """List supported files in the folder. `stable` is False while a file still
    looks like it's being written (modified within the last STABLE_SECONDS)."""
    root = Path(folder).expanduser()
    if not root.is_dir():
        return []
    now = time.time()
    out = []
    for f in sorted(root.iterdir()):
        if not f.is_file():
            continue
        try:
            fmt = localsource.detect_format(f.name)
        except localsource.LocalSourceError:
            continue
        stat = f.stat()
        out.append({
            "name": f.name,
            "path": str(f),
            "size": stat.st_size,
            "format": fmt,
            "stable": (now - stat.st_mtime) >= STABLE_SECONDS,
        })
    return out


def import_path(keystore: KeyStore, path: str) -> SourceManifest:
    p = Path(path).expanduser()
    if not p.is_file():
        raise localsource.LocalSourceError(f"file not found: {path}")
    fmt = localsource.detect_format(p.name)
    return localsource.import_bytes(keystore, p.name, fmt, p.read_bytes(), local_path=str(p))
