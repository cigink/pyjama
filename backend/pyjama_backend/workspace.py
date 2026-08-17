"""Application-managed workspace ("notebook") metadata (Phase 0 — Epic F; Sources refactor).

A workspace no longer owns any data directly — it is a declarative pipeline
(transform steps + row key) pointed at a *primary source* (sources.py), plus any
number of other sources referenced by join steps. Any source can be reused as
the primary or a join input across any number of workspaces (decoupled, notebook
model). The .enc files keep their names as a stable contract but are plaintext
JSON in Phase 0/1; encryption lands alongside the manifest in a later hardening
pass.
"""

from __future__ import annotations

import json
import os
import platform
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import app_data_dir


class WorkspaceError(Exception):
    pass


def workspaces_root() -> Path:
    return app_data_dir() / "workspaces"


def _workspace_dir(workspace_id: str) -> Path:
    return workspaces_root() / workspace_id


@dataclass
class Manifest:
    workspace_id: str
    name: str
    created_at: str
    primary_source_id: str | None = None
    row_key: list[str] = field(default_factory=list)
    pipeline_revision: int = 0
    pipeline: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        d = json.loads(text)
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def create(name: str, primary_source_id: str | None = None) -> Manifest:
    workspace_id = str(uuid.uuid4())
    root = _workspace_dir(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Windows":
        os.chmod(root, 0o700)

    manifest = Manifest(
        workspace_id=workspace_id,
        name=name,
        created_at=datetime.now(timezone.utc).isoformat(),
        primary_source_id=primary_source_id,
    )
    write_manifest(manifest)
    return manifest


def write_manifest(manifest: Manifest) -> None:
    root = _workspace_dir(manifest.workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.enc").write_text(manifest.to_json())


def read_manifest(workspace_id: str) -> Manifest:
    path = _workspace_dir(workspace_id) / "manifest.enc"
    if not path.exists():
        raise WorkspaceError(f"workspace {workspace_id} not found")
    return Manifest.from_json(path.read_text())


def list_workspaces() -> list[str]:
    root = workspaces_root()
    if not root.exists():
        return []
    out = [p.name for p in root.iterdir() if p.is_dir() and (p / "manifest.enc").exists()]
    out.sort()
    return out


def delete_workspace(workspace_id: str) -> None:
    import shutil

    d = _workspace_dir(workspace_id)
    if d.exists():
        shutil.rmtree(d)
