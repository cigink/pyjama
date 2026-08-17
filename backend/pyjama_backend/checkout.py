"""Encrypted checkout engine (Phase 2 + Sources refactor).

Streams a governed, reduced result from Databricks (Arrow + external links) into
encrypted Parquet partitions belonging to a *source* (sources.py) — not a
workspace. Sources are decoupled from workspaces: once checked out, a source can
be used as the primary dataset or a join input in any number of workspaces.
Never concatenates the full result in memory and never writes a plaintext
dataset file. Resumable via the operation journal.

Flow (IMPLEMENTATION_PLAN §8):
  submit ARROW_STREAM/EXTERNAL_LINKS → poll → for each chunk: fetch presigned
  link → download (no auth header) → decode Arrow → Parquet in memory → encrypt
  → write partition → journal it. Kill anytime; restart resumes missing chunks.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Callable

import pyarrow as pa
import pyarrow.parquet as pq

from . import crypto
from .databricks import (
    DatabricksClient,
    RestError,
    is_terminal,
    statement_manifest_chunks,
    statement_total_chunks,
)
from .dbsql import CompiledQuery
from .journal import CheckoutJournal
from .keystore import KeyStore
from .logging_setup import log
from .sources import SourceManifest, source_data_dir, write_manifest

# Default local-checkout size policy (§20.1). API allows far more; we cap low.
WARN_BYTES = 2 * 1024**3      # 2 GiB
BLOCK_BYTES = 10 * 1024**3    # 10 GiB

ProgressCb = Callable[[dict], None]


class CheckoutError(Exception):
    pass


@dataclass
class CheckoutResult:
    source_id: str
    row_count: int
    byte_count: int
    partition_files: int


def _decode_arrow(data: bytes) -> pa.Table:
    reader = pa.ipc.open_stream(pa.py_buffer(data))
    return reader.read_all()


def _write_encrypted_partition(table: pa.Table, wdek: bytes, path) -> int:
    """Write the table to Parquet in memory, encrypt, and persist ciphertext.
    Returns the encrypted byte size. No plaintext ever touches disk."""
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    plaintext = buf.getvalue()
    blob = crypto.encrypt(wdek, plaintext)
    path.write_bytes(blob)
    return len(blob)


def fetch_result_arrow(
    client: DatabricksClient,
    warehouse_id: str,
    sql: str,
    params,
    poll_budget_s: float = 120.0,
    cap_bytes: int = BLOCK_BYTES,
) -> pa.Table:
    """Run a governed SELECT and return the whole (reduced) result as one Arrow
    table. Used for small/bounded lookups (e.g. importing a second UC table as a
    join source) — the result should be a bounded set, so it is size-capped."""
    resp = client.submit_statement_arrow(warehouse_id, sql, params)
    statement_id = resp.get("statement_id", "")
    waited, delay = 0.0, 1.0
    while not is_terminal(resp["status"]["state"]):
        if waited >= poll_budget_s:
            raise CheckoutError("timed out waiting for statement")
        time.sleep(delay)
        waited += delay
        delay = min(delay * 2, 5.0)
        resp = client.get_statement(statement_id)
    state = resp["status"]["state"]
    if state != "SUCCEEDED":
        raise CheckoutError(f"statement {state}: " + resp["status"].get("error", {}).get("message", state))

    chunks = statement_manifest_chunks(resp)
    total_chunks = statement_total_chunks(resp) or len(chunks)
    total_bytes = sum(c.get("byte_count", 0) for c in chunks)
    if total_bytes > cap_bytes:
        raise CheckoutError(f"table is {total_bytes / 1024**3:.1f} GiB — too large to import as a join source; add filters")

    tables = []
    for idx in range(total_chunks):
        link = client.get_chunk_link(statement_id, idx)
        raw = client.download_external(link["external_link"])
        tables.append(_decode_arrow(raw))
    if not tables:
        return pa.table({})
    return pa.concat_tables(tables) if len(tables) > 1 else tables[0]


class CheckoutEngine:
    """Streams a governed UC checkout into a source's encrypted partitions."""

    def __init__(self, client: DatabricksClient, keystore: KeyStore):
        self._client = client
        self._keystore = keystore

    def run(
        self,
        source: SourceManifest,
        warehouse_id: str,
        query: CompiledQuery,
        operation_id: str,
        progress: ProgressCb | None = None,
        poll_budget_s: float = 300.0,
    ) -> CheckoutResult:
        source_id = source.source_id
        data_dir = source_data_dir(source_id)
        data_dir.mkdir(parents=True, exist_ok=True)

        wdek = crypto.load_or_create_wdek(self._keystore, source_id)

        journal = CheckoutJournal.load(source_id)
        # Resume when a prior attempt left a valid statement id — its already
        # written chunks are durable and will be skipped. FAILED is included so a
        # retry after a mid-download error doesn't re-download completed chunks.
        resuming = (
            journal is not None
            and bool(journal.statement_id)
            and journal.state in ("SUBMITTED", "DOWNLOADING", "FAILED")
        )
        if not resuming:
            journal = CheckoutJournal(container_id=source_id, operation_id=operation_id)

        def emit(**extra):
            if progress:
                progress({
                    "operation_id": operation_id,
                    "source_id": source_id,
                    "state": journal.state,
                    "completed_chunks": len(journal.completed_chunks),
                    "total_chunks": journal.total_chunks,
                    "row_count": journal.row_count,
                    "byte_count": journal.byte_count,
                    **extra,
                })

        try:
            # 1. Submit (or reuse an in-flight statement on resume).
            if resuming:
                resp = self._client.get_statement(journal.statement_id)
                log("resuming checkout", operation_id=operation_id, statement_id=journal.statement_id)
            else:
                resp = self._client.submit_statement_arrow(warehouse_id, query.sql, query.params)
                journal.mark_submitted(resp.get("statement_id", ""), None)
            emit()

            # 2. Poll to terminal.
            waited, delay = 0.0, 1.0
            while not is_terminal(resp["status"]["state"]):
                if waited >= poll_budget_s:
                    raise CheckoutError("timed out waiting for statement")
                time.sleep(delay)
                waited += delay
                delay = min(delay * 2, 5.0)
                resp = self._client.get_statement(journal.statement_id)
                emit()

            state = resp["status"]["state"]
            if state != "SUCCEEDED":
                msg = resp["status"].get("error", {}).get("message", state)
                raise CheckoutError(f"statement {state}: {msg}")

            # 3. Size policy on the manifest byte total (§20.1).
            chunks = statement_manifest_chunks(resp)
            total_chunks = statement_total_chunks(resp) or len(chunks)
            total_bytes = sum(c.get("byte_count", 0) for c in chunks)
            if total_bytes > BLOCK_BYTES:
                raise CheckoutError(
                    f"working set {total_bytes / 1024**3:.1f} GiB exceeds the {BLOCK_BYTES / 1024**3:.0f} GiB checkout limit"
                )
            if total_bytes > WARN_BYTES:
                log("large checkout", operation_id=operation_id, gib=round(total_bytes / 1024**3, 2))

            journal.total_chunks = total_chunks
            journal.mark_downloading()
            emit()

            # 4. Download + decode + encrypt each remaining chunk.
            for idx in range(total_chunks):
                if journal.is_chunk_done(idx):
                    continue
                link = self._client.get_chunk_link(journal.statement_id, idx)  # fresh presigned URL (handles expiry)
                raw = self._client.download_external(link["external_link"])
                table = _decode_arrow(raw)
                part_path = data_dir / f"source-{idx:05d}.parquet"
                enc_bytes = _write_encrypted_partition(table, wdek, part_path)
                journal.mark_chunk_done(idx, table.num_rows, enc_bytes)
                emit()

            # 5. Persist source manifest storage info.
            source.partition_files = len(journal.completed_chunks)
            source.row_count = journal.row_count
            source.logical_bytes = journal.byte_count
            source.encryption_key_id = crypto.wdek_key_name(source_id)
            write_manifest(source)

            journal.mark_complete()
            emit(state="COMPLETE")
            log("checkout complete", operation_id=operation_id, rows=journal.row_count, partitions=len(journal.completed_chunks))
            return CheckoutResult(
                source_id=source_id,
                row_count=journal.row_count,
                byte_count=journal.byte_count,
                partition_files=len(journal.completed_chunks),
            )
        except (RestError, CheckoutError) as e:
            journal.mark_failed(str(e))
            emit(state="FAILED", error=str(e))
            raise
