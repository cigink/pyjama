"""Commit write-back to Unity Catalog (Phase 7 — IMPLEMENTATION_PLAN §15/§16).

Stages the reviewed change set to a UC Volume as Parquet, then runs a governed
`MERGE INTO` (existing table) or `CREATE TABLE AS` (new table) in Databricks SQL.
Never touches Delta files directly. Checks the source Delta version before
committing and blocks on conflict.
"""

from __future__ import annotations

import io
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from .databricks import DatabricksClient, RestError, statement_columns, statement_rows
from .dbsql import quote_ident, quote_qualified


# Above this many rows the inline VALUES statement gets too big; fall back to
# staging the change set as a Parquet file on a UC Volume.
INLINE_ROW_CAP = 10_000


class CommitError(Exception):
    pass


def _sql_literal(v) -> str:
    """Render a Python value as a safe Databricks SQL literal. Strings are
    single-quote-escaped (Databricks does not use backslash escapes by default)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if "\x00" in s:
        raise CommitError("value contains a NUL byte")
    return "'" + s.replace("'", "''") + "'"


def _values_clause(change_table: pa.Table) -> tuple[str, list[str]]:
    """Build `(VALUES (...), (...)) AS s(col, ..., _op)` from the change set."""
    cols = list(change_table.column_names)
    data = {c: change_table.column(c).to_pylist() for c in cols}
    rows = []
    for i in range(change_table.num_rows):
        rows.append("(" + ", ".join(_sql_literal(data[c][i]) for c in cols) + ")")
    col_defs = ", ".join(quote_ident(c) for c in cols)
    return f"(VALUES {', '.join(rows)}) AS s({col_defs})", cols


class ConflictError(CommitError):
    def __init__(self, base_version: int, current_version: int):
        super().__init__(f"source changed since checkout (v{base_version} → v{current_version})")
        self.base_version = base_version
        self.current_version = current_version


def get_table_version(client: DatabricksClient, warehouse_id: str, table: str) -> int | None:
    """Current Delta version via DESCRIBE HISTORY (§16). None if unavailable."""
    try:
        resp = client.run_sql_sync(warehouse_id, f"DESCRIBE HISTORY {quote_qualified(table)} LIMIT 1")
    except RestError:
        return None
    cols = statement_columns(resp)
    rows = statement_rows(resp)
    if "version" not in cols or not rows:
        return None
    return int(rows[0][cols.index("version")])


def _staging_path(volume: str, commit_id: str) -> str:
    # volume like /Volumes/<catalog>/<schema>/<volume>
    return f"{volume.rstrip('/')}/app-staging/{commit_id}/changes.parquet"


def commit_change_set(
    client: DatabricksClient,
    warehouse_id: str,
    change_table: pa.Table,
    *,
    target_table: str,
    keys: list[str],
    source_columns: list[str],
    staging_volume: str,
    create_new: bool,
    base_version: int | None,
    source_table: str | None = None,
    progress=None,
) -> dict:
    """Stage + MERGE/CREATE. Returns commit metadata. Raises ConflictError if the
    source moved since checkout."""

    def emit(state: str):
        if progress:
            progress({"state": state})

    if change_table.num_rows == 0:
        raise CommitError(
            "No changes to the target's existing columns. If your pipeline added columns "
            "(join / formula), commit as a New table to keep them."
        )

    commit_id = str(uuid.uuid4())

    # 1. Conflict check: did the SOURCE table move since checkout? (§16). Only
    # meaningful for an existing-table MERGE (a new table can't be corrupted by
    # source drift), and only when a real base version was recorded at checkout.
    if not create_new and base_version and source_table:
        emit("checking version")
        current = get_table_version(client, warehouse_id, source_table)
        if current is not None and current != base_version:
            raise ConflictError(base_version, current)

    use_inline = change_table.num_rows <= INLINE_ROW_CAP
    if not use_inline and not staging_volume:
        raise CommitError(
            f"{change_table.num_rows} changed rows exceed the inline limit; set PYJAMA_STAGING_VOLUME "
            "to a /Volumes/<catalog>/<schema>/<volume> path to stage larger commits"
        )

    non_key = [c for c in source_columns if c not in keys]
    insert_cols = ", ".join(quote_ident(c) for c in source_columns)
    insert_vals = ", ".join(f"s.{quote_ident(c)}" for c in source_columns)
    set_clause = ", ".join(f"{quote_ident(c)} = s.{quote_ident(c)}" for c in non_key) or f"{quote_ident(keys[0])} = s.{quote_ident(keys[0])}"
    on = " AND ".join(f"t.{quote_ident(k)} = s.{quote_ident(k)}" for k in keys)

    if use_inline:
        # Route the write straight through the Statement Execution API using an
        # inline VALUES source — no Volume needed for bounded change sets.
        emit("merging")
        using, all_cols = _values_clause(change_table)
        if create_new:
            proj = ", ".join(quote_ident(c) for c in source_columns)
            sql = f"CREATE TABLE {quote_qualified(target_table)} AS SELECT {proj} FROM {using}"
        else:
            sql = (
                f"MERGE INTO {quote_qualified(target_table)} AS t USING {using} ON {on} "
                f"WHEN MATCHED AND s._op = 'UPDATE' THEN UPDATE SET {set_clause} "
                f"WHEN NOT MATCHED AND s._op = 'INSERT' THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
            )
        client.run_sql_sync(warehouse_id, sql)
    else:
        # Large change set: stage as Parquet on a UC Volume, then MERGE/CREATE.
        emit("staging")
        buf = io.BytesIO()
        pq.write_table(change_table, buf, compression="zstd")
        path = _staging_path(staging_volume, commit_id)
        client.upload_volume_file(path, buf.getvalue())
        try:
            emit("merging")
            if create_new:
                proj = ", ".join(quote_ident(c) for c in source_columns)
                sql = f"CREATE TABLE {quote_qualified(target_table)} AS SELECT {proj} FROM parquet.`{path}`"
            else:
                sql = (
                    f"MERGE INTO {quote_qualified(target_table)} AS t USING parquet.`{path}` AS s ON {on} "
                    f"WHEN MATCHED AND s._op = 'UPDATE' THEN UPDATE SET {set_clause} "
                    f"WHEN NOT MATCHED AND s._op = 'INSERT' THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
                )
            client.run_sql_sync(warehouse_id, sql)
        finally:
            client.delete_volume_file(path)

    new_version = get_table_version(client, warehouse_id, target_table)
    emit("done")
    return {
        "commit_id": commit_id,
        "target_table": target_table,
        "new_version": new_version,
        "row_count": change_table.num_rows,
        "created": create_new,
    }
