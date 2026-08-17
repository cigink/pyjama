import io
import shutil

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pyjama_backend import crypto, sources
from pyjama_backend.checkout import CheckoutEngine, CheckoutError
from pyjama_backend.dbsql import CompiledQuery
from pyjama_backend.journal import CheckoutJournal
from pyjama_backend.keystore import MemoryKeyStore


# ---- crypto ----
def test_wdek_encrypt_round_trip():
    ks = MemoryKeyStore()
    wdek = crypto.load_or_create_wdek(ks, "src-1")
    assert len(wdek) == 32
    # stable per source
    assert crypto.load_or_create_wdek(ks, "src-1") == wdek
    blob = crypto.encrypt(wdek, b"sensitive rows")
    assert b"sensitive rows" not in blob  # ciphertext, not plaintext
    assert crypto.decrypt(wdek, blob) == b"sensitive rows"
    # wrong key fails
    with pytest.raises(Exception):
        crypto.decrypt(crypto.new_wdek(), blob)


# ---- journal ----
def test_journal_persist_and_resume():
    src = sources.create_placeholder("Journal Test", "uc_table")
    j = CheckoutJournal(container_id=src.source_id, operation_id="op1")
    j.mark_submitted("stmt-1", total_chunks=3)
    j.mark_chunk_done(0, rows=10, num_bytes=100)
    reloaded = CheckoutJournal.load(src.source_id)
    assert reloaded.statement_id == "stmt-1"
    assert reloaded.is_chunk_done(0)
    assert not reloaded.is_chunk_done(1)
    assert reloaded.row_count == 10
    shutil.rmtree(sources.source_dir(src.source_id))


# ---- fake Databricks client emitting Arrow chunks ----
def _arrow_stream(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as w:
        w.write_table(table)
    return sink.getvalue().to_pybytes()


CHUNK_TABLES = [
    pa.table({"customer_id": [83728, 83729], "company": ["ACME", "Foo BV"]}),
    pa.table({"customer_id": [83730], "company": ["Bar GmbH"]}),
]


class FakeClient:
    def __init__(self, fail_first_download_of=None):
        self.download_calls = []
        self._fail_once = fail_first_download_of
        self._failed = set()

    def submit_statement_arrow(self, warehouse_id, sql, params):
        return self._terminal()

    def get_statement(self, statement_id):
        return self._terminal()

    def _terminal(self):
        return {
            "statement_id": "stmt-1",
            "status": {"state": "SUCCEEDED"},
            "manifest": {
                "total_chunk_count": len(CHUNK_TABLES),
                "chunks": [{"chunk_index": i, "row_count": t.num_rows, "byte_count": 100} for i, t in enumerate(CHUNK_TABLES)],
            },
        }

    def get_chunk_link(self, statement_id, chunk_index):
        return {"external_link": f"mock://chunk/{chunk_index}", "chunk_index": chunk_index}

    def download_external(self, url):
        idx = int(url.rsplit("/", 1)[1])
        self.download_calls.append(idx)
        if self._fail_once == idx and idx not in self._failed:
            self._failed.add(idx)
            raise RuntimeError("simulated network drop")
        return _arrow_stream(CHUNK_TABLES[idx])


def _query():
    return CompiledQuery(sql="SELECT * FROM `t`", params=[])


def test_checkout_writes_encrypted_partitions_no_plaintext():
    src = sources.create_placeholder("Checkout Test", "uc_table")
    ks = MemoryKeyStore()
    engine = CheckoutEngine(FakeClient(), ks)

    result = engine.run(src, "wh-1", _query(), operation_id="op-x")
    assert result.row_count == 3
    assert result.partition_files == 2

    data_dir = sources.source_data_dir(src.source_id)
    parts = sorted(data_dir.glob("source-*.parquet"))
    assert len(parts) == 2

    # Ciphertext on disk: the plaintext sentinel must NOT appear.
    for p in parts:
        raw = p.read_bytes()
        assert b"ACME" not in raw and b"Bar GmbH" not in raw

    # Decrypting with the WDEK yields readable Parquet.
    wdek = crypto.get_wdek(ks, src.source_id)
    plaintext = crypto.decrypt(wdek, parts[0].read_bytes())
    table = pq.read_table(io.BytesIO(plaintext))
    assert "ACME" in table.column("company").to_pylist()

    # Source manifest updated — independent of any workspace.
    man = sources.read_manifest(src.source_id)
    assert man.row_count == 3
    assert man.partition_files == 2
    assert man.encryption_key_id == crypto.wdek_key_name(src.source_id)

    shutil.rmtree(sources.source_dir(src.source_id))


def test_checkout_resumes_without_redownloading_done_chunks():
    src = sources.create_placeholder("Resume Test", "uc_table")
    ks = MemoryKeyStore()

    # First attempt fails on chunk 1 (chunk 0 already written + journaled).
    client = FakeClient(fail_first_download_of=1)
    engine = CheckoutEngine(client, ks)
    with pytest.raises(RuntimeError):
        engine.run(src, "wh-1", _query(), operation_id="op-a")
    assert client.download_calls == [0, 1]  # tried 0 (ok) then 1 (failed)

    # Resume: chunk 0 is durable, so only chunk 1 is downloaded again.
    client2 = FakeClient()
    engine2 = CheckoutEngine(client2, ks)
    result = engine2.run(src, "wh-1", _query(), operation_id="op-a")
    assert client2.download_calls == [1]  # chunk 0 skipped
    assert result.partition_files == 2
    assert result.row_count == 3

    shutil.rmtree(sources.source_dir(src.source_id))
