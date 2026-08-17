"""Exploration orchestration: question -> SQL -> validated result, with one
execution-guided repair attempt (Phase 10, P10.12-P10.16).

Ephemeral by design — nothing here mutates the workspace. Only an explicit
`promote()` call turns a result into a durable pipeline step, and it does not
depend on the model at all: the validated SQL is stored, not the question.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from . import ai_sql
from .ai_runtime import runtime as default_runtime


class ExplorationBlocked(Exception):
    """Policy-blocked request — surfaced verbatim, never repaired (P10.9)."""


@dataclass
class ExplorationResult:
    id: str
    question: str
    status: str  # SUCCESS | BLOCKED | INVALID | CANCELED
    generated_sql: str | None = None
    model_id: str = "ss-350m-sql-strict-q8"
    attempt_count: int = 0
    inference_ms: int = 0
    execution_ms: int = 0
    rows_returned: int = 0
    columns: list[str] = field(default_factory=list)
    preview_rows: list[list] = field(default_factory=list)
    error: str | None = None


class ExplorationService:
    def __init__(self, rt=None):
        self._rt = rt or default_runtime
        self._lock = threading.Lock()
        self._cancel_flags: dict[str, bool] = {}

    def cancel(self, request_id: str) -> None:
        with self._lock:
            self._cancel_flags[request_id] = True

    def _canceled(self, request_id: str) -> bool:
        with self._lock:
            return self._cancel_flags.pop(request_id, False)

    def ask(self, table, schema: list[dict], question: str) -> ExplorationResult:
        """`table` is the pyarrow Table backing `current`; `schema` is its
        [{"name","type"}] list from WorkspaceSession.step_output()."""
        request_id = str(uuid.uuid4())
        result = ExplorationResult(id=request_id, question=question, status="INVALID")

        con = ai_sql.restricted_connection(table)
        try:
            t0 = time.monotonic()
            sample_block = ai_sql.sample_rows_block(table)
            messages = ai_sql.build_prompt(schema, question, sample_block)
            raw_sql = self._rt.generate(messages)
            result.inference_ms += int((time.monotonic() - t0) * 1000)
            result.attempt_count = 1

            if self._canceled(request_id):
                return ExplorationResult(id=request_id, question=question, status="CANCELED")

            try:
                validated = ai_sql.validate_sql(raw_sql)
            except ai_sql.SqlPolicyError as e:
                result.status = "BLOCKED"
                result.error = str(e)
                return result

            try:
                t1 = time.monotonic()
                arrow_result = ai_sql.explain_and_execute(con, validated)
                result.execution_ms += int((time.monotonic() - t1) * 1000)
                result.generated_sql = validated
            except Exception as e:
                # Parser/binder failure only — one repair attempt (P10.12).
                repair_messages = ai_sql.build_repair_prompt(schema, question, validated, str(e))
                t2 = time.monotonic()
                raw_sql2 = self._rt.generate(repair_messages)
                result.inference_ms += int((time.monotonic() - t2) * 1000)
                result.attempt_count = 2

                if self._canceled(request_id):
                    return ExplorationResult(id=request_id, question=question, status="CANCELED")

                try:
                    validated2 = ai_sql.validate_sql(raw_sql2)
                except ai_sql.SqlPolicyError as e2:
                    result.status = "BLOCKED"
                    result.error = str(e2)
                    return result

                try:
                    t3 = time.monotonic()
                    arrow_result = ai_sql.explain_and_execute(con, validated2)
                    result.execution_ms += int((time.monotonic() - t3) * 1000)
                    result.generated_sql = validated2
                except Exception as e2:
                    result.status = "INVALID"
                    result.error = f"could not answer this question: {e2}"
                    return result

            cols = list(arrow_result.column_names)
            col_lists = [arrow_result.column(c).to_pylist() for c in cols]
            rows = [list(r) for r in zip(*col_lists)] if col_lists and arrow_result.num_rows else []
            result.status = "SUCCESS"
            result.columns = cols
            result.rows_returned = arrow_result.num_rows
            result.preview_rows = rows[:500]
            return result
        finally:
            con.close()
            with self._lock:
                self._cancel_flags.pop(request_id, False)


def promote_sql(sql: str) -> str:
    """Re-validate at save time — promotion must never trust a client-supplied
    "already validated" flag (P10.16)."""
    return ai_sql.validate_sql(sql)


exploration_service = ExplorationService()
