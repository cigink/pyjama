"""Resumable operation journal (Phase 2 — Epic K, IMPLEMENTATION_PLAN §8.7/§18.3).

Records enough to resume a checkout after a crash or an expired presigned URL:
the statement id, total chunk count, and which chunk indexes are durably written.
Persisted as JSON and fsync'd after each irreversible transition. Contents are
non-secret (ids + indexes), so plaintext is acceptable; encryption can be added
alongside the manifest later.

Keyed by ``container_id`` — a source id (sources.py). Sources are the unit of
checkout/download; workspaces only reference a source's id.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .sources import source_dir


@dataclass
class CheckoutJournal:
    container_id: str
    operation_id: str
    op_type: str = "CHECKOUT"
    state: str = "PENDING"  # PENDING | SUBMITTED | DOWNLOADING | COMPLETE | FAILED
    statement_id: str | None = None
    total_chunks: int | None = None
    completed_chunks: list[int] = field(default_factory=list)
    row_count: int = 0
    byte_count: int = 0
    error: str | None = None

    # ---- persistence ----
    def _path(self) -> Path:
        return source_dir(self.container_id) / "operation-journal.enc"

    def save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write + fsync so a crash mid-checkout can resume from durable state.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(self), f)
            f.flush()
            os.fsync(f.fileno())

    @classmethod
    def load(cls, container_id: str) -> "CheckoutJournal | None":
        path = source_dir(container_id) / "operation-journal.enc"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or "operation_id" not in data:
            return None
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    # ---- transitions ----
    def mark_submitted(self, statement_id: str, total_chunks: int | None) -> None:
        self.statement_id = statement_id
        self.total_chunks = total_chunks
        self.state = "SUBMITTED"
        self.save()

    def mark_downloading(self) -> None:
        self.state = "DOWNLOADING"
        self.save()

    def mark_chunk_done(self, index: int, rows: int, num_bytes: int) -> None:
        if index not in self.completed_chunks:
            self.completed_chunks.append(index)
            self.completed_chunks.sort()
            self.row_count += rows
            self.byte_count += num_bytes
            self.save()

    def is_chunk_done(self, index: int) -> bool:
        return index in self.completed_chunks

    def mark_complete(self) -> None:
        self.state = "COMPLETE"
        self.save()

    def mark_failed(self, error: str) -> None:
        self.state = "FAILED"
        self.error = error
        self.save()
