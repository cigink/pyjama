"""Transform pipeline compiler (Phase 4 — Epics Q & R).

Compiles a declarative, linear step list into chained DuckDB CTEs over the
workspace source relation (IMPLEMENTATION_PLAN §10.2). Injection-safe: identifiers
are double-quoted and validated against the running column set; user values are
bound parameters (``?``), never interpolated.

Returns a WITH-clause + final relation name so the caller can append
ORDER BY / LIMIT / OFFSET for windowed preview.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .formula import compile_formula

VALUELESS_OPS = {"is_null", "is not null", "is_not_null", "is null"}


class PipelineError(Exception):
    pass


@dataclass
class Step:
    id: str
    type: str
    config: dict = field(default_factory=dict)
    enabled: bool = True
    # Which earlier step's output this step reads from. None means "the step
    # immediately before this one in the list" (the original linear chain) —
    # any earlier step's id makes the pipeline branch into a tree/DAG.
    input_id: str | None = None

    def config_hash(self) -> str:
        payload = json.dumps({"type": self.type, "config": self.config, "input_id": self.input_id}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass
class Compiled:
    with_clause: str
    final_rel: str
    params: list
    columns: list[str]


# ---- per-step compilers: (step, prev_rel, cols) -> (body_sql, new_cols, params) ----

def _op_sql(op: str) -> str:
    return {
        "eq": "=", "equals": "=", "=": "=",
        "ne": "!=", "not equals": "!=", "!=": "!=",
        "gt": ">", "greater than": ">", ">": ">",
        "lt": "<", "less than": "<", "<": "<",
        "before": "<", "after": ">",
    }.get(op, "")


def _compile_filter(step: Step, prev: str, cols: list[str], types: dict[str, str]):
    conds = step.config.get("conditions", [])
    if not conds:
        return f"SELECT * FROM {prev}", cols, []
    combine = " OR " if step.config.get("combine") == "or" else " AND "
    clauses, params = [], []
    for c in conds:
        col = c["column"]
        if col not in cols:
            raise PipelineError(f"filter references unknown column: {col}")
        op = c.get("op", "eq")
        qcol = _ident(col)
        # Values arrive as strings; cast the bound param to the column's type so
        # numeric/date columns compare correctly (DuckDB won't compare BIGINT to VARCHAR).
        coltype = types.get(col, "VARCHAR")
        if op in ("is_null", "is null"):
            clauses.append(f"{qcol} IS NULL")
        elif op in ("is_not_null", "is not null"):
            clauses.append(f"{qcol} IS NOT NULL")
        elif op == "contains":
            clauses.append(f"CAST({qcol} AS VARCHAR) LIKE ?")
            params.append(f"%{c.get('value', '')}%")
        elif op in ("in_list", "in list"):
            items = [x.strip() for x in str(c.get("value", "")).split(",") if x.strip()]
            if not items:
                raise PipelineError("IN list has no values")
            placeholders = ", ".join(f"CAST(? AS {coltype})" for _ in items)
            clauses.append(f"{qcol} IN ({placeholders})")
            params.extend(items)
        else:
            sop = _op_sql(op)
            if not sop:
                raise PipelineError(f"unknown operator: {op}")
            clauses.append(f"{qcol} {sop} CAST(? AS {coltype})")
            params.append(c.get("value", ""))
    return f"SELECT * FROM {prev} WHERE {combine.join(clauses)}", cols, params


def _compile_select(step: Step, prev: str, cols: list[str]):
    chosen = step.config.get("columns", [])
    for c in chosen:
        if c not in cols:
            raise PipelineError(f"select references unknown column: {c}")
    if not chosen:
        raise PipelineError("select step has no columns")
    proj = ", ".join(_ident(c) for c in chosen)
    return f"SELECT {proj} FROM {prev}", list(chosen), []


def _compile_rename(step: Step, prev: str, cols: list[str]):
    old, new = step.config.get("from"), step.config.get("to")
    if old not in cols:
        raise PipelineError(f"rename references unknown column: {old}")
    if not new:
        raise PipelineError("rename needs a target name")
    new_cols = [new if c == old else c for c in cols]
    proj = ", ".join(f"{_ident(c)} AS {_ident(new)}" if c == old else _ident(c) for c in cols)
    return f"SELECT {proj} FROM {prev}", new_cols, []


def _compile_formula(step: Step, prev: str, cols: list[str]):
    name = step.config.get("name")
    expr = step.config.get("expression", "")
    if not name:
        raise PipelineError("formula needs a column name")
    frag, params = compile_formula(expr, set(cols))
    new_cols = cols + ([name] if name not in cols else [])
    return f"SELECT *, ({frag}) AS {_ident(name)} FROM {prev}", new_cols, params


def _compile_deduplicate(step: Step, prev: str, cols: list[str]):
    keys = step.config.get("keys") or ([step.config["key"]] if step.config.get("key") else [])
    if not keys:
        raise PipelineError("deduplicate needs a key")
    for k in keys:
        if k not in cols:
            raise PipelineError(f"deduplicate references unknown column: {k}")
    keep = step.config.get("keep", "latest")
    order_by = step.config.get("order_by") or []
    if not order_by and keep == "latest":
        # default: newest by updated_at if present
        if "updated_at" in cols:
            order_by = [{"column": "updated_at", "direction": "desc"}]
    terms = []
    for ob in order_by:
        c = ob.get("column")
        if c not in cols:
            continue
        direction = "DESC" if ob.get("direction") == "desc" else "ASC"
        terms.append(f"{_ident(c)} {direction}")
    if keep == "first":
        terms = terms or [f"{_ident(keys[0])} ASC"]
    order_sql = ("ORDER BY " + ", ".join(terms)) if terms else "ORDER BY 1"
    part = ", ".join(_ident(k) for k in keys)
    body = f"SELECT * FROM {prev} QUALIFY ROW_NUMBER() OVER (PARTITION BY {part} {order_sql}) = 1"
    return body, cols, []


def _compile_join(step: Step, prev: str, cols: list[str], local_sources: dict):
    sid = step.config.get("local_source_id")
    src = local_sources.get(sid)
    if not src:
        raise PipelineError("join references an unknown local source")
    join_type = "LEFT" if step.config.get("join_type", "left") == "left" else "INNER"
    keys = step.config.get("keys", [])
    if not keys:
        raise PipelineError("join needs at least one key")
    src_cols = src["columns"]
    for k in keys:
        if k["left"] not in cols:
            raise PipelineError(f"join left key not found: {k['left']}")
        if k["right"] not in src_cols:
            raise PipelineError(f"join right key not found: {k['right']}")
    on = " AND ".join(f"{prev}.{_ident(k['left'])} = r.{_ident(k['right'])}" for k in keys)
    right_keys = {k["right"] for k in keys}
    # Bring in the right side's non-key columns; skip names that would collide.
    add_cols = [c for c in src_cols if c not in right_keys and c not in cols]
    proj = f"{prev}.*"
    if add_cols:
        proj += ", " + ", ".join(f"r.{_ident(c)}" for c in add_cols)
    body = f"SELECT {proj} FROM {prev} {join_type} JOIN {src['rel']} r ON {on}"
    return body, cols + add_cols, []


def _compile_manual_edit(step: Step, prev: str, cols: list[str]):
    """Overlay manually-edited cell values by row key, without mutating the
    source partitions (PRD §8.8). Each edit is {key: {col: val, ...}, column,
    value}. Grouped by edited column; each group becomes a small inline VALUES
    relation LEFT JOINed on the key, with COALESCE(override, original)."""
    edits = step.config.get("edits", [])
    keys = step.config.get("keys", [])
    if not edits:
        return f"SELECT * FROM {prev}", cols, []
    if not keys:
        raise PipelineError("manual edits need a row key")
    for k in keys:
        if k not in cols:
            raise PipelineError(f"manual edit key not found: {k}")

    by_column: dict[str, list[dict]] = {}
    for e in edits:
        col = e.get("column")
        if col not in cols:
            raise PipelineError(f"manual edit references unknown column: {col}")
        by_column.setdefault(col, []).append(e)

    body = f"SELECT * FROM {prev}"
    params: list = []
    for i, (col, col_edits) in enumerate(by_column.items()):
        alias = f"me{i}"
        key_defs = ", ".join(_ident(k) for k in keys)
        rows_sql = []
        for e in col_edits:
            row_vals = ["?"] * len(keys) + ["?"]
            rows_sql.append(f"({', '.join(row_vals)})")
            for k in keys:
                params.append(e["key"].get(k))
            params.append(e.get("value"))
        values_sql = f"(VALUES {', '.join(rows_sql)}) AS {alias}({key_defs}, {_ident('__val')})"
        on = " AND ".join(f"t.{_ident(k)} = {alias}.{_ident(k)}" for k in keys)
        qcol = _ident(col)
        body = f"SELECT t.* REPLACE (COALESCE({alias}.{_ident('__val')}, t.{qcol}) AS {qcol}) FROM ({body}) t LEFT JOIN {values_sql} ON {on}"
    return body, cols, params


def _compile_replace(step: Step, prev: str, cols: list[str]):
    col = step.config.get("column")
    if col not in cols:
        raise PipelineError(f"replace references unknown column: {col}")
    mappings = step.config.get("mappings", [])
    if not mappings:
        return f"SELECT * FROM {prev}", cols, []
    whens, params = [], []
    for m in mappings:
        whens.append("WHEN ? THEN ?")
        params.append(m.get("from", ""))
        params.append(m.get("to", ""))
    qcol = _ident(col)
    case_expr = f"CASE {qcol} {' '.join(whens)} ELSE {qcol} END"
    return f"SELECT * REPLACE ({case_expr} AS {qcol}) FROM {prev}", cols, params


def _compile_sql_transform(step: Step, prev: str, cols: list[str], local_sources: dict | None = None):
    """A promoted local-AI exploration or Explore analysis (Phase 10 P10.16 /
    Phase 12 P12.10). The stored SQL was validated against the AST allowlist
    at promotion time; re-validate here too — defense in depth against a
    hand-edited manifest — then rebind its `current` relation reference onto
    the prior CTE. If the step's config carries a `local_source_id` (a
    promoted Explore Join), that relation is allowlisted here too — it must
    stay resolvable across every future recompile, not just at promotion."""
    from . import ai_sql

    sql = step.config.get("sql", "")
    join_source_id = step.config.get("local_source_id")
    extra_relations = None
    if join_source_id:
        src = (local_sources or {}).get(join_source_id)
        if src is None:
            raise PipelineError(f"sql_transform step references an unknown local source: {join_source_id}")
        extra_relations = {src["rel"]}
    try:
        validated = ai_sql.validate_sql(sql, extra_relations=extra_relations)
    except ai_sql.SqlPolicyError as e:
        raise PipelineError(f"sql_transform step failed re-validation: {e}") from e

    import sqlglot
    from sqlglot import expressions as exp

    tree = sqlglot.parse_one(validated, read="duckdb")
    cte_aliases = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.name.lower() == ai_sql.RELATION_NAME and table.name.lower() not in cte_aliases:
            table.set("this", exp.to_identifier(prev))
            table.set("db", None)
    rebased = tree.sql(dialect="duckdb")

    # Best-effort output column list from the SELECT projection list (used for
    # sort/filter targeting downstream); falls back to the prior columns if
    # the statement is a bare `SELECT *`.
    top_select = tree if isinstance(tree, exp.Select) else tree.this
    new_cols = cols
    if isinstance(top_select, exp.Select):
        projections = top_select.expressions
        if projections and not any(isinstance(p, exp.Star) for p in projections):
            new_cols = [p.alias_or_name for p in projections]

    return f"SELECT * FROM ({rebased}) AS _sql_step", new_cols, []


_COMPILERS = {
    "select_columns": _compile_select,
    "rename": _compile_rename,
    "formula": _compile_formula,
    "deduplicate": _compile_deduplicate,
    "replace": _compile_replace,
}


def compile_pipeline(
    steps: list[Step],
    source_columns: list[str],
    source_rel: str = "ws",
    up_to: int | None = None,
    column_types: dict[str, str] | None = None,
    local_sources: dict | None = None,
) -> Compiled:
    """Compile steps[0..up_to] into CTEs. Step 0 is expected to be the source
    (type 'source'); it becomes s0. Disabled steps are skipped.

    Each step reads from the step named by its `input_id` — defaulting to the
    step immediately before it in the list, i.e. the original linear chain.
    Any step can instead point at any *earlier* step's id, turning the
    pipeline into a tree: several steps can branch off the same ancestor. The
    final output is always the last compiled step in list order (§ up_to)."""
    types = dict(column_types or {})
    lsrc = dict(local_sources or {})
    ctes = [f"s0 AS (SELECT * FROM {source_rel})"]
    params: list = []
    idx = 0

    # step_id -> (cte_name, output_cols), populated strictly in list order so
    # an input_id can only ever resolve to an already-compiled, earlier step
    # — forward references and cycles are impossible by construction.
    node: dict[str, tuple[str, list[str]]] = {}
    if steps and steps[0].type == "source":
        node[steps[0].id] = ("s0", list(source_columns))
    last_rel, last_cols = "s0", list(source_columns)

    end = len(steps) - 1 if up_to is None else min(up_to, len(steps) - 1)
    for i, step in enumerate(steps):
        if i > end:
            break
        if step.type == "source" or not step.enabled:
            continue

        if step.input_id is not None:
            if step.input_id not in node:
                raise PipelineError(f"step {step.id} has an invalid input: {step.input_id}")
            prev, cols = node[step.input_id]
        else:
            prev, cols = last_rel, last_cols

        if step.type == "filter":
            body, cols, p = _compile_filter(step, prev, cols, types)
        elif step.type == "join_file":
            body, cols, p = _compile_join(step, prev, cols, lsrc)
        elif step.type == "manual_edit":
            body, cols, p = _compile_manual_edit(step, prev, cols)
        elif step.type == "sql_transform":
            body, cols, p = _compile_sql_transform(step, prev, cols, lsrc)
        else:
            compiler = _COMPILERS.get(step.type)
            if compiler is None:
                raise PipelineError(f"unknown step type: {step.type}")
            body, cols, p = compiler(step, prev, cols)
        idx += 1
        name = f"s{idx}"
        ctes.append(f"{name} AS ({body})")
        params.extend(p)
        node[step.id] = (name, cols)
        last_rel, last_cols = name, cols

    with_clause = "WITH " + ",\n".join(ctes)
    return Compiled(with_clause=with_clause, final_rel=last_rel, params=params, columns=last_cols)
