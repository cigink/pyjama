import pyarrow as pa
import pytest

import pyjama_backend.commit as commitmod
from pyjama_backend.commit import CommitError, ConflictError, commit_change_set


class FakeClient:
    def __init__(self, version=5):
        self.version = version
        self.sqls = []
        self.uploaded = []

    def run_sql_sync(self, wh, sql, params=None):
        self.sqls.append(sql)
        if sql.startswith("DESCRIBE HISTORY"):
            return {"manifest": {"schema": {"columns": [{"name": "version"}]}}, "result": {"data_array": [[str(self.version)]]}, "status": {"state": "SUCCEEDED"}}
        if sql.startswith("MERGE") or sql.startswith("CREATE"):
            self.version += 1
            return {"status": {"state": "SUCCEEDED"}}
        return {"status": {"state": "SUCCEEDED"}}

    def upload_volume_file(self, path, data):
        self.uploaded.append(path)

    def delete_volume_file(self, path):
        pass


CHANGE = pa.table({"id": [1, 2], "name": ["a'b", "b"], "_op": ["UPDATE", "INSERT"]})


def test_inline_merge_no_volume_needed():
    c = FakeClient(version=5)
    res = commit_change_set(
        c, "wh", CHANGE,
        target_table="main.crm.customers", keys=["id"], source_columns=["id", "name"],
        staging_volume="", create_new=False, base_version=5,  # no volume!
    )
    merge = next(s for s in c.sqls if s.startswith("MERGE"))
    assert "USING (VALUES (" in merge  # inline source
    assert "'a''b'" in merge           # string safely escaped
    assert "WHEN MATCHED AND s._op = 'UPDATE' THEN UPDATE SET `name` = s.`name`" in merge
    assert "WHEN NOT MATCHED AND s._op = 'INSERT' THEN INSERT (`id`, `name`)" in merge
    assert "SET *" not in merge
    assert not c.uploaded  # never touched a volume
    assert res["new_version"] == 6


def test_inline_create_new_table():
    c = FakeClient(version=0)
    res = commit_change_set(
        c, "wh", CHANGE,
        target_table="workspace.default.customers_v2", keys=["id"], source_columns=["id", "name"],
        staging_volume="", create_new=True, base_version=None,
    )
    create = next(s for s in c.sqls if s.startswith("CREATE TABLE"))
    assert "CREATE TABLE `workspace`.`default`.`customers_v2` AS SELECT `id`, `name` FROM (VALUES" in create
    assert res["created"] is True and not c.uploaded


def test_conflict_blocks():
    c = FakeClient(version=7)  # source moved to v7
    with pytest.raises(ConflictError):
        commit_change_set(c, "wh", CHANGE, target_table="main.crm.customers", keys=["id"], source_columns=["id", "name"], staging_volume="", create_new=False, base_version=5, source_table="main.crm.customers")
    assert not any(s.startswith("MERGE") for s in c.sqls)


def test_large_change_set_needs_volume(monkeypatch):
    monkeypatch.setattr(commitmod, "INLINE_ROW_CAP", 1)  # force "too large"
    c = FakeClient()
    with pytest.raises(CommitError):
        commit_change_set(c, "wh", CHANGE, target_table="t", keys=["id"], source_columns=["id", "name"], staging_volume="", create_new=False, base_version=None)


def test_large_change_set_uses_volume(monkeypatch):
    monkeypatch.setattr(commitmod, "INLINE_ROW_CAP", 1)
    c = FakeClient(version=3)
    res = commit_change_set(c, "wh", CHANGE, target_table="main.crm.customers", keys=["id"], source_columns=["id", "name"], staging_volume="/Volumes/main/crm/stg", create_new=False, base_version=3)
    assert c.uploaded  # staged to the volume
    assert any(s.startswith("MERGE") and "parquet.`/Volumes/" in s for s in c.sqls)
    assert res["new_version"] == 4
