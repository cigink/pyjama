"""Shared source registry (decoupled from workspaces/notebooks).

A *source* is one encrypted, locally-materialized dataset — either a governed
Unity Catalog checkout or an imported local file (CSV/XLSX/Parquet). Sources are
stored once, independent of any workspace, and can be used as the primary
dataset or a join input in any number of workspaces ("notebooks").

Layout: ``<app-data>/PyJama/sources/<source_id>/``
  - ``manifest.enc``      — JSON metadata (plaintext today; see workspace.py note)
  - ``operation-journal.enc`` — resumable checkout journal (UC-table sources)
  - ``data/*.parquet``    — encrypted partitions (one or many)
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import app_data_dir


class SourceError(Exception):
    pass


def sources_root() -> Path:
    return app_data_dir() / "sources"


def source_dir(source_id: str) -> Path:
    return sources_root() / source_id


def source_data_dir(source_id: str) -> Path:
    return source_dir(source_id) / "data"


@dataclass
class SourceManifest:
    source_id: str
    name: str
    kind: str  # "uc_table" | "csv" | "xlsx" | "parquet"
    created_at: str
    refreshed_at: str
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    logical_bytes: int = 0
    partition_files: int = 0
    encryption_key_id: str | None = None
    # Origin, for refresh:
    uc_table: str | None = None
    uc_columns: list[str] = field(default_factory=list)
    uc_filters: list[dict] = field(default_factory=list)
    uc_base_version: int | None = None
    local_path: str | None = None  # original file path, if still reachable

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "SourceManifest":
        return cls(**json.loads(text))


def write_manifest(m: SourceManifest) -> None:
    d = source_dir(m.source_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.enc").write_text(m.to_json())


def read_manifest(source_id: str) -> SourceManifest:
    path = source_dir(source_id) / "manifest.enc"
    if not path.exists():
        raise SourceError(f"source {source_id} not found")
    return SourceManifest.from_json(path.read_text())


def create_placeholder(name: str, kind: str) -> SourceManifest:
    """Reserve a new source id and write an initial (empty) manifest. Callers
    fill in data + finalize via write_manifest after import/checkout."""
    sid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    for sub in ("", "data"):
        (source_dir(sid) / sub).mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(source_dir(sid), 0o700)
    m = SourceManifest(source_id=sid, name=name, kind=kind, created_at=now, refreshed_at=now)
    write_manifest(m)
    return m


def list_sources() -> list[SourceManifest]:
    root = sources_root()
    if not root.exists():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and (p / "manifest.enc").exists():
            try:
                out.append(read_manifest(p.name))
            except (SourceError, json.JSONDecodeError, TypeError):
                continue
    out.sort(key=lambda m: m.refreshed_at, reverse=True)
    return out


def delete_source(source_id: str, keystore=None) -> None:
    import shutil

    d = source_dir(source_id)
    if d.exists():
        shutil.rmtree(d)
    if keystore is not None:
        from . import crypto

        keystore.delete(crypto.wdek_key_name(source_id))


def clear_data(source_id: str) -> None:
    """Remove existing partitions before a refresh writes fresh ones."""
    d = source_data_dir(source_id)
    if d.exists():
        for f in d.glob("*.parquet"):
            f.unlink()
    d.mkdir(parents=True, exist_ok=True)
