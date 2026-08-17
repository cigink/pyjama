import io
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

from pyjama_backend import crypto, sources, workspace
from pyjama_backend.keystore import MemoryKeyStore
from pyjama_backend.query import SessionCache, WorkspaceSession


def _write_encrypted_partition(wdek: bytes, path, table: pa.Table) -> None:
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    path.write_bytes(crypto.encrypt(wdek, buf.getvalue()))


def _make_source(ks: MemoryKeyStore, rows_per_part: list[int], name: str = "Query Test") -> sources.SourceManifest:
    src = sources.create_placeholder(name, "uc_table")
    wdek = crypto.load_or_create_wdek(ks, src.source_id)
    data_dir = sources.source_data_dir(src.source_id)
    n = 0
    for i, count in enumerate(rows_per_part):
        ids = list(range(n, n + count))
        names = [f"co-{j}" for j in ids]
        _write_encrypted_partition(wdek, data_dir / f"source-{i:05d}.parquet", pa.table({"id": ids, "company": names}))
        n += count
    return src


def _make_workspace(ks: MemoryKeyStore, rows_per_part: list[int]) -> workspace.Manifest:
    """A source with data, plus a notebook whose pipeline reads it as primary —
    the shape WorkspaceSession expects (decoupled: source + workspace)."""
    src = _make_source(ks, rows_per_part)
    m = workspace.create("Query Test", primary_source_id=src.source_id)
    m.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
    workspace.write_manifest(m)
    return m


def _cleanup(m: workspace.Manifest) -> None:
    if m.primary_source_id:
        shutil.rmtree(sources.source_dir(m.primary_source_id), ignore_errors=True)
    shutil.rmtree(workspace.workspaces_root() / m.workspace_id, ignore_errors=True)


def test_windowed_query_and_total():
    ks = MemoryKeyStore()
    m = _make_workspace(ks, [200, 55])  # 255 rows across 2 partitions
    sess = WorkspaceSession(m.workspace_id, ks)
    assert sess.total == 255
    assert {c["name"] for c in sess.schema()} == {"id", "company"}

    page = sess.query(offset=0, limit=100)
    assert len(page["rows"]) == 100
    assert page["total"] == 255
    assert page["rows"][0][0] == 0

    page2 = sess.query(offset=250, limit=100)
    assert len(page2["rows"]) == 5  # only 5 left
    sess.close()
    _cleanup(m)


def test_sorted_query_pushes_order_to_sql():
    ks = MemoryKeyStore()
    m = _make_workspace(ks, [10])
    sess = WorkspaceSession(m.workspace_id, ks)
    page = sess.query(offset=0, limit=3, sort=[{"column": "id", "direction": "desc"}])
    assert [r[0] for r in page["rows"]] == [9, 8, 7]
    sess.close()
    _cleanup(m)


def test_concurrent_queries_do_not_crash():
    # A DuckDB connection isn't concurrency-safe; the session must serialize.
    import threading

    ks = MemoryKeyStore()
    m = _make_workspace(ks, [500])
    sess = WorkspaceSession(m.workspace_id, ks)
    errors = []

    def hammer():
        try:
            for _ in range(25):
                sess.query(offset=0, limit=50, sort=[{"column": "id", "direction": "desc"}])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    sess.close()
    _cleanup(m)


def test_distinct_values():
    ks = MemoryKeyStore()
    src = sources.create_placeholder("Distinct Test", "csv")
    wdek = crypto.load_or_create_wdek(ks, src.source_id)
    _write_encrypted_partition(wdek, sources.source_data_dir(src.source_id) / "source-00000.parquet", pa.table({"country": ["NL", "NL", "DE", "NL", "DE"]}))
    m = workspace.create("Distinct Test", primary_source_id=src.source_id)
    m.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
    workspace.write_manifest(m)

    sess = WorkspaceSession(m.workspace_id, ks)
    d = sess.distinct_values("country")
    assert d["total_distinct"] == 2
    assert not d["truncated"]
    values = {v["value"]: v["count"] for v in d["values"]}
    assert values == {"NL": 3, "DE": 2}
    sess.close()
    _cleanup(m)


def test_column_stats():
    ks = MemoryKeyStore()
    src = sources.create_placeholder("Stats Test", "csv")
    wdek = crypto.load_or_create_wdek(ks, src.source_id)
    _write_encrypted_partition(
        wdek, sources.source_data_dir(src.source_id) / "source-00000.parquet",
        pa.table({"revenue": [10, 20, None, 20, 40], "country": ["NL", "NL", "DE", "NL", None]}),
    )
    m = workspace.create("Stats Test", primary_source_id=src.source_id)
    m.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
    workspace.write_manifest(m)

    sess = WorkspaceSession(m.workspace_id, ks)
    rev = sess.column_stats("revenue")
    assert rev["total"] == 5 and rev["nulls"] == 1 and rev["distinct"] == 3
    assert rev["min"] == 10 and rev["max"] == 40
    country = sess.column_stats("country")
    assert country["nulls"] == 1
    assert country["top_values"][0]["value"] == "NL" and country["top_values"][0]["count"] == 3
    sess.close()
    _cleanup(m)


def test_session_cache_reuses_and_evicts():
    ks = MemoryKeyStore()
    m = _make_workspace(ks, [5])
    cache = SessionCache(ks)
    a = cache.get(m.workspace_id)
    b = cache.get(m.workspace_id)
    assert a is b  # cached
    cache.evict(m.workspace_id)
    c = cache.get(m.workspace_id)
    assert c is not a
    cache.evict(m.workspace_id)
    _cleanup(m)


def test_source_reused_across_two_workspaces():
    """The core decoupling guarantee: one source, multiple independent
    notebooks, no duplicated data."""
    ks = MemoryKeyStore()
    src = _make_source(ks, [7], name="Shared Source")
    m1 = workspace.create("Notebook A", primary_source_id=src.source_id)
    m1.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
    workspace.write_manifest(m1)
    m2 = workspace.create("Notebook B", primary_source_id=src.source_id)
    m2.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
    workspace.write_manifest(m2)

    s1 = WorkspaceSession(m1.workspace_id, ks)
    s2 = WorkspaceSession(m2.workspace_id, ks)
    assert s1.total == 7 and s2.total == 7
    # only one copy of the data on disk
    parts = list(sources.source_data_dir(src.source_id).glob("*.parquet"))
    assert len(parts) == 1
    s1.close()
    s2.close()
    shutil.rmtree(sources.source_dir(src.source_id), ignore_errors=True)
    shutil.rmtree(workspace.workspaces_root() / m1.workspace_id, ignore_errors=True)
    shutil.rmtree(workspace.workspaces_root() / m2.workspace_id, ignore_errors=True)
