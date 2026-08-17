"""Windowed query engine over a workspace's encrypted partitions (Phase 3).

Opens a workspace by decrypting its Parquet partitions and registering them as a
DuckDB relation, then answers *windowed* preview queries — offset/limit/sort
pushed into SQL so the UI only ever receives a page, never the whole dataset
(IMPLEMENTATION_PLAN §11). Sessions are cached per workspace.

The decrypted data lives in backend memory (bounded by the checkout size policy,
§20). A later optimization can switch to DuckDB-native encrypted Parquet scans so
even the backend never holds the full set; the query contract here stays the same.
"""

from __future__ import annotations

import io
import threading

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import crypto
from .keystore import KeyStore
from .sources import source_data_dir


def _duck_ident(ident: str) -> str:
    """Quote a DuckDB identifier with double quotes (backticks are Databricks)."""
    return '"' + ident.replace('"', '""') + '"'


def _arrow_to_duck(arrow_type: str) -> str:
    t = arrow_type.lower()
    if "int" in t:
        return "BIGINT"
    if "float" in t or "double" in t or "decimal" in t:
        return "DOUBLE"
    if "bool" in t:
        return "BOOLEAN"
    if "timestamp" in t:
        return "TIMESTAMP"
    if "date" in t:
        return "DATE"
    return "VARCHAR"


def _load_source_table(source_id: str, keystore: KeyStore) -> pa.Table:
    data_dir = source_data_dir(source_id)
    parts = sorted(data_dir.glob("source-*.parquet"))
    wdek = crypto.get_wdek(keystore, source_id)
    tables: list[pa.Table] = []
    for p in parts:
        plaintext = crypto.decrypt(wdek, p.read_bytes())
        tables.append(pq.read_table(io.BytesIO(plaintext)))
    return pa.concat_tables(tables) if len(tables) > 1 else (tables[0] if tables else pa.table({}))


class WorkspaceSession:
    """A workspace ("notebook") is a declarative pipeline over a *primary
    source* plus whichever other sources its join steps reference — all
    resolved from the shared, decoupled source registry (sources.py), not from
    anything owned by this workspace."""

    def __init__(self, workspace_id: str, keystore: KeyStore):
        from .workspace import read_manifest as read_workspace_manifest

        self.workspace_id = workspace_id
        self._keystore = keystore
        manifest = read_workspace_manifest(workspace_id)
        self.primary_source_id = manifest.primary_source_id

        if manifest.primary_source_id:
            self._table = _load_source_table(manifest.primary_source_id, keystore)
        else:
            self._table = pa.table({})

        self._con = duckdb.connect(database=":memory:")
        self._con.register("ws", self._table)

        # Resolve every source referenced by a step's config.local_source_id —
        # any source in the shared registry, regardless of which workspace
        # originally imported/checked it out (decoupled notebook model). Not
        # gated on step type: both the durable `join_file` step and a
        # promoted Explore Join (a `sql_transform` step whose SQL text
        # references the same relation name) need this resolved identically.
        self._local_sources: dict[str, dict] = {}
        self._ls_tables: list[pa.Table] = []  # keep refs alive for DuckDB
        referenced_ids = {
            step.get("config", {}).get("local_source_id")
            for step in (manifest.pipeline or [])
            if step.get("config", {}).get("local_source_id")
        }
        for sid in referenced_ids:
            try:
                t = _load_source_table(sid, keystore)
            except Exception:
                continue
            rel = f"ls_{sid.replace('-', '')}"
            self._con.register(rel, t)
            self._ls_tables.append(t)
            self._local_sources[sid] = {"rel": rel, "columns": list(t.column_names)}
        # A DuckDB connection is NOT safe for concurrent use. The UI fires several
        # queries in parallel (schema + counts + preview), all on FastAPI's
        # threadpool, so every execute on this connection must be serialized —
        # otherwise the native engine corrupts its heap and aborts the process.
        self._lock = threading.RLock()
        self._total = self._table.num_rows
        self._columns = list(self._table.column_names)
        self._types = {f.name: str(f.type) for f in self._table.schema}
        self._duck_types = {name: _arrow_to_duck(t) for name, t in self._types.items()}

    def schema(self) -> list[dict]:
        return [{"name": c, "type": self._types.get(c, "")} for c in self._columns]

    def schema_columns(self) -> list[str]:
        return list(self._columns)

    def column_stats(self, column: str) -> dict:
        """Cheap per-column profile for the header stats popover (P9.12)."""
        if column not in self._columns:
            raise ValueError(f"unknown column: {column}")
        qcol = _duck_ident(column)
        duck_type = self._duck_types.get(column, "VARCHAR")
        is_numeric = duck_type in ("BIGINT", "DOUBLE")
        with self._lock:
            total, nulls, distinct = self._con.execute(
                f"SELECT count(*), count(*) FILTER (WHERE {qcol} IS NULL), count(DISTINCT {qcol}) FROM ws"
            ).fetchone()
            min_v = max_v = None
            if is_numeric:
                min_v, max_v = self._con.execute(f"SELECT min({qcol}), max({qcol}) FROM ws").fetchone()
            top = self._con.execute(
                f"SELECT {qcol} AS v, count(*) AS n FROM ws GROUP BY {qcol} ORDER BY n DESC LIMIT 5"
            ).fetchall()
        return {
            "column": column,
            "type": duck_type,
            "total": total,
            "nulls": nulls,
            "null_pct": round(100 * nulls / total, 1) if total else 0.0,
            "distinct": distinct,
            "min": min_v,
            "max": max_v,
            "top_values": [{"value": r[0], "count": r[1]} for r in top],
        }

    def distinct_values(self, column: str, limit: int = 50) -> dict:
        """Distinct values of a source column (for filter/replace pickers). Only
        offered for columns cheap enough to be useful as a checkbox list."""
        if column not in self._columns:
            raise ValueError(f"unknown column: {column}")
        qcol = _duck_ident(column)
        with self._lock:
            total_distinct = self._con.execute(f"SELECT count(DISTINCT {qcol}) FROM ws").fetchone()[0]
            rows = self._con.execute(
                f"SELECT {qcol} AS v, count(*) AS n FROM ws GROUP BY {qcol} ORDER BY n DESC LIMIT {int(limit)}"
            ).fetchall()
        return {
            "column": column,
            "total_distinct": total_distinct,
            "truncated": total_distinct > limit,
            "values": [{"value": r[0], "count": r[1]} for r in rows],
        }

    @property
    def total(self) -> int:
        return self._total

    def query_pipeline(
        self,
        steps: list,
        up_to: int | None,
        offset: int = 0,
        limit: int = 500,
        sort: list[dict] | None = None,
    ) -> dict:
        """Compile the pipeline up to `up_to` and return a window of its output.
        `steps` is a list of pipeline.Step. Falls back to the raw source when the
        pipeline is empty/source-only."""
        from .pipeline import compile_pipeline

        c = compile_pipeline(steps, self._columns, up_to=up_to, column_types=self._duck_types, local_sources=self._local_sources)
        base = f"{c.with_clause} SELECT * FROM {c.final_rel}"

        order = ""
        if sort:
            terms = []
            for s in sort:
                col = s.get("column")
                if col in c.columns:
                    direction = "DESC" if s.get("direction") == "desc" else "ASC"
                    terms.append(f"{_duck_ident(col)} {direction}")
            if terms:
                order = " ORDER BY " + ", ".join(terms)

        sql = f"{base}{order} LIMIT {int(limit)} OFFSET {int(offset)}"
        with self._lock:
            rel = self._con.execute(sql, c.params).arrow()
            total = self._con.execute(f"{c.with_clause} SELECT count(*) FROM {c.final_rel}", c.params).fetchone()[0]
        cols = list(rel.column_names)
        col_lists = [rel.column(cc).to_pylist() for cc in cols]
        rows = [list(r) for r in zip(*col_lists)] if col_lists and rel.num_rows else []
        return {"columns": cols, "rows": rows, "offset": offset, "total": total, "output_columns": c.columns}

    def pipeline_row_count(self, steps: list, up_to: int | None) -> int:
        from .pipeline import compile_pipeline

        c = compile_pipeline(steps, self._columns, up_to=up_to, column_types=self._duck_types, local_sources=self._local_sources)
        with self._lock:
            return self._con.execute(f"{c.with_clause} SELECT count(*) FROM {c.final_rel}", c.params).fetchone()[0]

    def verify_row_key(self, keys: list[str]) -> dict:
        """Check the chosen row key is unique on BASE (§13.1)."""
        for k in keys:
            if k not in self._columns:
                return {"unique": False, "error": f"unknown column: {k}"}
        if not keys:
            return {"unique": False, "error": "no key columns"}
        qkeys = ", ".join(_duck_ident(k) for k in keys)
        sql = f"SELECT {qkeys}, count(*) AS n FROM ws GROUP BY {qkeys} HAVING count(*) > 1 LIMIT 1"
        with self._lock:
            row = self._con.execute(sql).fetchone()
        return {"unique": row is None, "duplicate": None if row is None else list(row[:-1])}

    def diff(self, steps: list, keys: list[str]) -> dict:
        from .diff import DiffError, build_diff
        from .pipeline import compile_pipeline

        c = compile_pipeline(steps, self._columns, column_types=self._duck_types, local_sources=self._local_sources)
        try:
            d = build_diff(c.with_clause, c.final_rel, self._columns, c.columns, keys)
        except DiffError as e:
            raise
        with self._lock:
            added, deleted, modified, unchanged = self._con.execute(d["counts_sql"], c.params).fetchone()
            sample = self._con.execute(d["samples_sql"], c.params + [20]).arrow()

        non_key = d["non_key"]
        cols = sample.column_names
        data = {cc: sample.column(cc).to_pylist() for cc in cols}
        samples = []
        for i in range(sample.num_rows):
            key_vals = {k: data[k][i] for k in keys}
            changes = []
            for col in non_key:
                before = data.get(f"{col}__before", [None] * sample.num_rows)[i]
                after = data.get(f"{col}__after", [None] * sample.num_rows)[i]
                if before != after:
                    changes.append({"column": col, "before": before, "after": after})
            samples.append({"key": key_vals, "changes": changes})
        return {"added": added, "deleted": deleted, "modified": modified, "unchanged": unchanged, "samples": samples}

    def validate(self, steps: list, rules: list[dict]) -> dict:
        from .pipeline import compile_pipeline
        from .validation import ValidationError, compile_rule

        c = compile_pipeline(steps, self._columns, column_types=self._duck_types, local_sources=self._local_sources)
        final_cols = set(c.columns)
        final_types = {col: self._duck_types.get(col, "VARCHAR") for col in c.columns}

        per_rule = []
        error_pass, error_params = [], []
        with self._lock:
            for r in rules:
                try:
                    expr, rp = compile_rule(r, final_cols, final_types)
                except ValidationError as e:
                    per_rule.append({"id": r.get("id"), "invalid": None, "error": str(e), "severity": r.get("severity", "error")})
                    continue
                invalid = self._con.execute(f"{c.with_clause} SELECT count(*) FROM {c.final_rel} WHERE NOT ({expr})", c.params + rp).fetchone()[0]
                per_rule.append({"id": r.get("id"), "invalid": invalid, "error": None, "severity": r.get("severity", "error")})
                if r.get("severity", "error") == "error":
                    error_pass.append(f"({expr})")
                    error_params.extend(rp)

            total = self._con.execute(f"{c.with_clause} SELECT count(*) FROM {c.final_rel}", c.params).fetchone()[0]
            if error_pass:
                cond = " AND ".join(error_pass)
                invalid_rows = self._con.execute(f"{c.with_clause} SELECT count(*) FROM {c.final_rel} WHERE NOT ({cond})", c.params + error_params).fetchone()[0]
                sample = self._con.execute(f"{c.with_clause} SELECT * FROM {c.final_rel} WHERE NOT ({cond}) LIMIT 20", c.params + error_params).arrow()
            else:
                invalid_rows = 0
                sample = self._con.execute(f"{c.with_clause} SELECT * FROM {c.final_rel} LIMIT 0", c.params).arrow()

        scols = list(sample.column_names)
        slists = [sample.column(cc).to_pylist() for cc in scols]
        srows = [list(r) for r in zip(*slists)] if slists and sample.num_rows else []
        return {
            "total": total,
            "valid": total - invalid_rows,
            "invalid": invalid_rows,
            "blocking": invalid_rows > 0,
            "per_rule": per_rule,
            "failed_columns": scols,
            "failed_rows": srows,
        }

    def step_output(self, steps: list, up_to: int | None, cap: int = 200_000) -> tuple["pa.Table", list[dict]]:
        """Materialized output of the pipeline up to `up_to` (or the full
        pipeline), plus its column schema — the `current` relation exposed to
        the local AI assistant (Phase 10)."""
        from .pipeline import compile_pipeline

        c = compile_pipeline(steps, self._columns, up_to=up_to, column_types=self._duck_types, local_sources=self._local_sources)
        with self._lock:
            table = self._con.execute(f"{c.with_clause} SELECT * FROM {c.final_rel} LIMIT {int(cap)}", c.params).arrow()
        schema = [{"name": f.name, "type": self._duck_types.get(f.name, "VARCHAR")} for f in table.schema]
        return table, schema

    def _ensure_local_source(self, source_id: str) -> None:
        """Register a source on demand (Phase 12, P12.10) — Explore Join
        needs to reference sources the durable pipeline hasn't used yet
        (that's the point of exploring before deciding to promote), unlike
        `__init__`'s registration which only covers already-referenced ones."""
        if source_id in self._local_sources:
            return
        with self._lock:
            if source_id in self._local_sources:  # re-check inside the lock
                return
            t = _load_source_table(source_id, self._keystore)
            rel = f"ls_{source_id.replace('-', '')}"
            self._con.register(rel, t)
            self._ls_tables.append(t)
            self._local_sources[source_id] = {"rel": rel, "columns": list(t.column_names)}

    def run_analysis(self, steps: list, up_to: int | None, spec) -> dict:
        """Execute an AnalysisSpec against the output of the pipeline up to
        `up_to` (Phase 11, P11.7/§39). `spec` is an analysis_spec.AnalysisSpec.
        Ephemeral — never mutates the pipeline or bumps its revision."""
        from . import analysis_spec as aspec
        from .pipeline import compile_pipeline

        if spec.join:
            self._ensure_local_source(spec.join.local_source_id)

        c = compile_pipeline(steps, self._columns, up_to=up_to, column_types=self._duck_types, local_sources=self._local_sources)
        compiled = aspec.compile_analysis(spec, c.final_rel, c.columns, self._duck_types, local_sources=self._local_sources)
        # Merge the analysis's own CTE stages (if any) into the pipeline's
        # single WITH clause — two back-to-back WITH keywords is a syntax
        # error, so this can't just concatenate compiled.sql onto c.with_clause.
        with_clause = f"{c.with_clause}, {', '.join(compiled.cte_defs)}" if compiled.cte_defs else c.with_clause
        full_sql = f"{with_clause} {compiled.final_select}"
        with self._lock:
            arrow_result = self._con.execute(full_sql, c.params + compiled.params).arrow()
        cols = list(arrow_result.column_names)
        col_lists = [arrow_result.column(cc).to_pylist() for cc in cols]
        rows = [list(r) for r in zip(*col_lists)] if col_lists and arrow_result.num_rows else []
        return {
            "columns": cols,
            "rows": rows,
            "row_count": arrow_result.num_rows,
            "generated_sql": full_sql,
            "visualization_hint": aspec.visualization_hint(spec, compiled, self._duck_types),
        }

    def promote_analysis_sql(self, steps: list, up_to: int | None, spec) -> str:
        """Compile an AnalysisSpec into standalone, literal-valued SQL against
        `current` and AST-validate it — the "Keep as workflow" bridge
        (Phase 11, P11.16). Raises PipelineError / AnalysisError /
        ai_sql.SqlPolicyError; never touches the pipeline itself — the
        caller appends the returned SQL as a new sql_transform step."""
        from . import ai_sql
        from . import analysis_spec as aspec
        from .pipeline import compile_pipeline

        if spec.join:
            self._ensure_local_source(spec.join.local_source_id)
        c = compile_pipeline(steps, self._columns, up_to=up_to, column_types=self._duck_types, local_sources=self._local_sources)
        compiled = aspec.compile_analysis(spec, ai_sql.RELATION_NAME, c.columns, self._duck_types, inline_values=True, local_sources=self._local_sources)
        extra_relations = {self._local_sources[spec.join.local_source_id]["rel"]} if spec.join else None
        return ai_sql.validate_sql(compiled.sql, extra_relations=extra_relations)

    def full_output(self, steps: list, cap: int = 10_000) -> "pa.Table":
        """The complete final pipeline output (for creating a new table).
        Capped — larger writes should use the staged-volume path."""
        from .pipeline import compile_pipeline

        c = compile_pipeline(steps, self._columns, column_types=self._duck_types, local_sources=self._local_sources)
        with self._lock:
            return self._con.execute(f"{c.with_clause} SELECT * FROM {c.final_rel} LIMIT {int(cap) + 1}", c.params).arrow()

    def build_change_set(self, steps: list, keys: list[str]) -> "pa.Table":
        """Build the staged change set (added + modified rows) as an Arrow table:
        the source columns plus an `_op` column (INSERT / UPDATE). Compared on the
        source columns that survive to the output, keyed by `keys` (§13/§15)."""
        from .diff import DiffError
        from .pipeline import compile_pipeline

        if not keys:
            raise DiffError("a row key is required to build a change set")
        c = compile_pipeline(steps, self._columns, column_types=self._duck_types, local_sources=self._local_sources)
        shared = [col for col in self._columns if col in c.columns]
        for k in keys:
            if k not in shared:
                raise DiffError(f"row key {k} not present in both source and output")
        non_key = [col for col in shared if col not in keys]

        sel = ", ".join(_duck_ident(col) for col in shared)
        join_on = " AND ".join(f"f.{_duck_ident(k)} = b.{_duck_ident(k)}" for k in keys)
        corr_b = " AND ".join(f"b.{_duck_ident(k)} = f.{_duck_ident(k)}" for k in keys)
        distinct = " OR ".join(f"f.{_duck_ident(col)} IS DISTINCT FROM b.{_duck_ident(col)}" for col in non_key) or "FALSE"
        fsel = ", ".join(f"f.{_duck_ident(col)} AS {_duck_ident(col)}" for col in shared)

        sql = (
            f"{c.with_clause}, b AS (SELECT {sel} FROM ws), f AS (SELECT {sel} FROM {c.final_rel}) "
            f"SELECT {fsel}, 'INSERT' AS _op FROM f WHERE NOT EXISTS (SELECT 1 FROM b WHERE {corr_b}) "
            f"UNION ALL "
            f"SELECT {fsel}, 'UPDATE' AS _op FROM f JOIN b ON {join_on} WHERE {distinct}"
        )
        with self._lock:
            return self._con.execute(sql, c.params).arrow()

    def query(self, offset: int = 0, limit: int = 500, sort: list[dict] | None = None) -> dict:
        sql = "SELECT * FROM ws"
        if sort:
            order_terms = []
            for s in sort:
                col = s.get("column")
                if col not in self._types:  # only known columns; quoted for safety
                    continue
                direction = "DESC" if s.get("direction") == "desc" else "ASC"
                order_terms.append(f"{_duck_ident(col)} {direction}")
            if order_terms:
                sql += " ORDER BY " + ", ".join(order_terms)
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"

        with self._lock:
            rel = self._con.execute(sql).arrow()
        cols = list(rel.column_names)
        col_lists = [rel.column(c).to_pylist() for c in cols]
        rows = [list(r) for r in zip(*col_lists)] if col_lists and rel.num_rows else []
        return {"columns": cols, "rows": rows, "offset": offset, "total": self._total}

    def close(self) -> None:
        self._con.close()


class SessionCache:
    """Thread-safe cache of open workspace sessions."""

    def __init__(self, keystore: KeyStore):
        self._keystore = keystore
        self._sessions: dict[str, WorkspaceSession] = {}
        self._lock = threading.Lock()

    def get(self, workspace_id: str) -> WorkspaceSession:
        with self._lock:
            sess = self._sessions.get(workspace_id)
            if sess is None:
                sess = WorkspaceSession(workspace_id, self._keystore)
                self._sessions[workspace_id] = sess
            return sess

    def evict(self, workspace_id: str) -> None:
        with self._lock:
            sess = self._sessions.pop(workspace_id, None)
        if sess:
            sess.close()
