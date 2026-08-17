import io
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

from pyjama_backend import crypto, sources, workspace
from pyjama_backend.keystore import MemoryKeyStore
from pyjama_backend.pipeline import Step
from pyjama_backend.query import WorkspaceSession

SRC = pa.table({
    "customer_id": [1, 2, 3, 4],
    "country": ["NL", "NL", "DE", "NL"],
    "revenue": [100, 50, 90, 0],
})


def _ws():
    ks = MemoryKeyStore()
    src = sources.create_placeholder("Diff Test", "uc_table")
    wdek = crypto.load_or_create_wdek(ks, src.source_id)
    buf = io.BytesIO()
    pq.write_table(SRC, buf, compression="zstd")
    (sources.source_data_dir(src.source_id) / "source-00000.parquet").write_bytes(crypto.encrypt(wdek, buf.getvalue()))
    m = workspace.create("Diff Test", primary_source_id=src.source_id)
    m.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
    workspace.write_manifest(m)
    return ks, m


def _cleanup(m):
    if m.primary_source_id:
        shutil.rmtree(sources.source_dir(m.primary_source_id), ignore_errors=True)
    shutil.rmtree(workspace.workspaces_root() / m.workspace_id, ignore_errors=True)


def test_row_key_uniqueness():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    assert sess.verify_row_key(["customer_id"])["unique"] is True
    res = sess.verify_row_key(["country"])
    assert res["unique"] is False and res["duplicate"] is not None
    sess.close(); _cleanup(m)


def test_diff_added_modified_deleted():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    # replace DE->Germany (modifies customer_id=3), filter revenue>0 (deletes id=4)
    steps = [
        Step(id="src", type="source"),
        Step(id="f", type="filter", config={"conditions": [{"column": "revenue", "op": "greater than", "value": "0"}]}),
        Step(id="rp", type="replace", config={"column": "country", "mappings": [{"from": "DE", "to": "Germany"}]}),
    ]
    d = sess.diff(steps, ["customer_id"])
    assert d["deleted"] == 1   # id=4 filtered out
    assert d["modified"] == 1  # id=3 country changed
    assert d["added"] == 0
    assert d["unchanged"] == 2
    # sample shows the field-level change
    mod = next(s for s in d["samples"] if s["changes"])
    change = mod["changes"][0]
    assert change["column"] == "country" and change["before"] == "DE" and change["after"] == "Germany"
    sess.close(); _cleanup(m)


def test_validation_error_blocks():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    rules = [
        {"id": "rev_pos", "column": "revenue", "kind": "gt", "value": "0", "severity": "error"},
    ]
    res = sess.validate(steps, rules)
    assert res["total"] == 4
    assert res["invalid"] == 1  # revenue=0 row fails
    assert res["valid"] == 3
    assert res["blocking"] is True
    assert res["per_rule"][0]["invalid"] == 1
    sess.close(); _cleanup(m)


def test_validation_warning_does_not_block():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    rules = [{"id": "rev_pos", "column": "revenue", "kind": "gt", "value": "0", "severity": "warning"}]
    res = sess.validate(steps, rules)
    assert res["blocking"] is False
    assert res["per_rule"][0]["invalid"] == 1  # still reported
    sess.close(); _cleanup(m)
