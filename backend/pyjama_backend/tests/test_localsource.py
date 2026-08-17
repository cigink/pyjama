import io
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

from pyjama_backend import crypto, localsource, sources, watchfolder
from pyjama_backend.keystore import MemoryKeyStore


def test_csv_import_encrypted_and_standalone():
    """A local file import creates a standalone source — not tied to any
    workspace — reusable across notebooks."""
    ks = MemoryKeyStore()
    csv = b"region_key,region_name\n0,AFRICA\n1,AMERICA\n"
    src = localsource.import_bytes(ks, "regions.csv", "csv", csv)
    assert src.columns == ["region_key", "region_name"]
    assert src.row_count == 2
    assert src.kind == "csv"

    # stored encrypted (no plaintext sentinel, not a raw parquet)
    path = sources.source_data_dir(src.source_id) / "source-00000.parquet"
    raw = path.read_bytes()
    assert not raw.startswith(b"PAR1")
    assert b"AFRICA" not in raw

    # decrypts back to a readable table
    wdek = crypto.get_wdek(ks, src.source_id)
    t = pq.read_table(io.BytesIO(crypto.decrypt(wdek, path.read_bytes())))
    assert "AFRICA" in t.column("region_name").to_pylist()

    # recorded in the shared registry, independent of any workspace
    man = sources.read_manifest(src.source_id)
    assert man.source_id == src.source_id
    assert any(m.source_id == src.source_id for m in sources.list_sources())

    shutil.rmtree(sources.source_dir(src.source_id))


def test_parquet_import():
    ks = MemoryKeyStore()
    buf = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2], "v": ["a", "b"]}), buf)
    src = localsource.import_bytes(ks, "x.parquet", "parquet", buf.getvalue())
    assert src.row_count == 2
    shutil.rmtree(sources.source_dir(src.source_id))


def test_refresh_from_path(tmp_path):
    ks = MemoryKeyStore()
    p = tmp_path / "lookup.csv"
    p.write_text("k,v\n1,one\n2,two\n")
    src = localsource.import_bytes(ks, "lookup.csv", "csv", p.read_bytes(), local_path=str(p))
    assert src.row_count == 2

    p.write_text("k,v\n1,one\n2,two\n3,three\n")
    refreshed = localsource.refresh_from_path(ks, src.source_id)
    assert refreshed.row_count == 3
    assert refreshed.refreshed_at >= src.refreshed_at

    shutil.rmtree(sources.source_dir(src.source_id))


def test_watch_scan_stability(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "note.txt").write_text("ignore me")
    files = watchfolder.scan(str(tmp_path))
    names = {f["name"] for f in files}
    assert "a.csv" in names
    assert "note.txt" not in names  # unsupported type filtered
    assert all("format" in f and "stable" in f for f in files)
