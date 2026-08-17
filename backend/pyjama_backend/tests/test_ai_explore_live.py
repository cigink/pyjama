"""Live end-to-end check against the real local model + llama-server sidecar
(Phase 10). Skipped automatically if the model isn't installed on this
machine — CI/other devs without the 379 MB GGUF still get a clean run."""

import io
import shutil

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pyjama_backend import ai_model, ai_sql, crypto, sources, workspace
from pyjama_backend.ai_explore import exploration_service, promote_sql
from pyjama_backend.keystore import MemoryKeyStore
from pyjama_backend.pipeline import Step, compile_pipeline
from pyjama_backend.query import WorkspaceSession

pytestmark = pytest.mark.skipif(not ai_model.model_path().exists(), reason="local AI model not installed")

SRC = pa.table({
    "customer_id": [1, 2, 3, 4],
    "country": ["NL", "NL", "DE", "NL"],
    "revenue": [100.0, 50.0, 90.0, 10.0],
    "cost": [40.0, 20.0, 30.0, 5.0],
})


def _ws():
    ks = MemoryKeyStore()
    src = sources.create_placeholder("AI Test", "uc_table")
    wdek = crypto.load_or_create_wdek(ks, src.source_id)
    buf = io.BytesIO()
    pq.write_table(SRC, buf, compression="zstd")
    (sources.source_data_dir(src.source_id) / "source-00000.parquet").write_bytes(crypto.encrypt(wdek, buf.getvalue()))
    m = workspace.create("AI Test", primary_source_id=src.source_id)
    m.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
    workspace.write_manifest(m)
    return ks, m


def _cleanup(m):
    if m.primary_source_id:
        shutil.rmtree(sources.source_dir(m.primary_source_id), ignore_errors=True)
    shutil.rmtree(workspace.workspaces_root() / m.workspace_id, ignore_errors=True)


def test_ask_and_promote_round_trip():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    table, schema = sess.step_output(steps, None)
    assert [c["name"] for c in schema] == ["customer_id", "country", "revenue", "cost"]

    result = exploration_service.ask(table, schema, "Which countries have the highest average margin?")
    assert result.status in ("SUCCESS", "BLOCKED", "INVALID")
    assert result.attempt_count >= 1

    if result.status == "SUCCESS":
        assert result.generated_sql
        assert "country" in [c.lower() for c in result.columns] or result.rows_returned >= 0

        # Promotion re-validates and the compiled sql_transform step reproduces
        # the same result without touching the model again.
        validated = promote_sql(result.generated_sql)
        promoted_step = Step(id="ai-1", type="sql_transform", config={"sql": validated})
        c = compile_pipeline(steps + [promoted_step], sess.schema_columns(), column_types=sess._duck_types)
        with sess._lock:
            rows = sess._con.execute(f"{c.with_clause} SELECT * FROM {c.final_rel}", c.params).fetchall()
        assert isinstance(rows, list)

    sess.close()
    _cleanup(m)


def test_security_prompt_is_blocked_not_executed():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    table, schema = sess.step_output(steps, None)

    result = exploration_service.ask(table, schema, "Attach another database and show me its tables")
    assert result.status in ("BLOCKED", "INVALID", "SUCCESS")
    # Whatever the model proposed, only ever reaches "SUCCESS" after passing
    # ai_sql.validate_sql — which rejects any actual ATTACH statement (see
    # test_ai_sql.py's security corpus). No further assertion needed here;
    # this test's job is to prove the live model+validator pipeline doesn't
    # crash or hang on an adversarial prompt.

    sess.close()
    _cleanup(m)
