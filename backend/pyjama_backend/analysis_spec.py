"""AnalysisSpec: the central abstraction of Explore (Phase 11, P11.7).

A declarative, non-AI description of an aggregation/filter/sort — the same
object whether it came from direct manipulation, the visual builder, SQL, or
natural language (§10). Compiles deterministically to parameterized DuckDB
SQL against a caller-supplied relation name; never interpolates values.

Identical AnalysisSpec -> byte-identical SQL. All column references are
validated against the active step's real output columns before compiling.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .expression import Expression, ExpressionError as _ExpressionError, NUMERIC_TYPES, compile_expression, literal_sql as _expr_literal_sql

TEMPORAL_TYPES = {"DATE", "TIMESTAMP"}

AGGREGATIONS = {
    "avg": "AVG",
    "sum": "SUM",
    "count": "COUNT",
    "count_distinct": "COUNT",  # rendered with DISTINCT below
    "min": "MIN",
    "max": "MAX",
}

_OP_SQL = {
    "=": "=", "eq": "=", "equals": "=",
    "!=": "!=", "ne": "!=", "not equals": "!=",
    ">": ">", "gt": ">", "greater than": ">",
    "<": "<", "lt": "<", "less than": "<",
    ">=": ">=", "<=": "<=",
    "contains": "LIKE",
    "is_null": "IS NULL", "is null": "IS NULL",
    "is_not_null": "IS NOT NULL", "is not null": "IS NOT NULL",
}

VALUELESS_OPS = {"is_null", "is null", "is_not_null", "is not null"}


class AnalysisError(Exception):
    pass


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass
class Measure:
    column: str
    aggregation: str = "count"
    alias: str | None = None

    def output_name(self) -> str:
        return self.alias or f"{self.aggregation}_{self.column}"


@dataclass
class FilterCond:
    column: str
    operator: str
    value: object = None


@dataclass
class SortSpec:
    column: str
    direction: str = "asc"


@dataclass
class DeriveColumn:
    """A row-level computed column (Phase 12, P12.3 — §8.2). Available to
    every later clause in the same spec (filters/dimensions/measures/sort) —
    it becomes just another column."""
    name: str
    expr: Expression
    result_type: str = "VARCHAR"


@dataclass
class WindowFrame:
    """Simplified frame spec covering the doc's named recipes (§8.8):
    Running total = preceding=None (UNBOUNDED); Moving average = preceding=N.
    `following` stops at CURRENT ROW (0) — forward-looking frames aren't
    needed by any current recipe and are deliberately not supported yet."""
    unit: str = "rows"  # rows | range
    preceding: int | None = None  # None = UNBOUNDED PRECEDING
    following: int = 0  # 0 = CURRENT ROW


@dataclass
class WindowExpr:
    """A window-operator expression (Phase 12, P12.6 — §8.8, Appendix A.4).
    `function_id` must be registry category "window" or "aggregate" (a plain
    aggregate becomes a window function when combined with OVER(...))."""
    function_id: str
    args: list[Expression] = field(default_factory=list)
    partition_by: list[str] = field(default_factory=list)
    order_by: list[SortSpec] = field(default_factory=list)
    frame: WindowFrame | None = None
    alias: str = "window_result"


@dataclass
class JoinSpec:
    """Explore-side Join (Phase 12, P12.10 — §8.5). `local_source_id` is
    resolved by the caller against the shared source registry (the same
    `local_sources` dict WorkspaceSession already builds for the durable
    `join_file` step) — this operator never sees a filesystem path."""
    local_source_id: str
    join_type: str = "left"  # inner | left | right | full | semi | anti
    keys: list[tuple[str, str]] = field(default_factory=list)  # (left_column, right_column)


JOIN_TYPES = {"inner": "INNER JOIN", "left": "LEFT JOIN", "right": "RIGHT JOIN", "full": "FULL JOIN", "semi": "SEMI JOIN", "anti": "ANTI JOIN"}


@dataclass
class AnalysisSpec:
    dimensions: list[str] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    filters: list[FilterCond] = field(default_factory=list)
    sort: list[SortSpec] = field(default_factory=list)
    derive: list[DeriveColumn] = field(default_factory=list)
    join: JoinSpec | None = None
    distinct: bool = False
    having: list[FilterCond] = field(default_factory=list)
    window: list[WindowExpr] = field(default_factory=list)
    window_derive: list[DeriveColumn] = field(default_factory=list)
    qualify: list[FilterCond] = field(default_factory=list)
    limit: int = 500


@dataclass
class CompiledAnalysis:
    sql: str  # full standalone statement — "WITH ... SELECT ..." if staged, else just the SELECT
    params: list
    output_columns: list[str]
    is_raw_rows: bool
    is_aggregate: bool  # single-row, no dimensions, exactly one measure
    cte_defs: list[str]  # each "name AS (...)" fragment — empty if no staging was needed
    final_select: str  # the trailing SELECT alone, for callers merging into their own WITH clause


def _validate_column(col: str, columns: list[str], context: str) -> None:
    if col not in columns:
        raise AnalysisError(f"{context} references unknown column: {col}")


def _sql_literal(value: object, coltype: str) -> str:
    """Safely inline a value as a SQL literal (for promoted steps, whose SQL
    must be a self-contained string re-validated on every pipeline compile —
    no separate params list survives a save). Shared with expression.py's
    Derive literal inlining — same requirement, one implementation."""
    try:
        return _expr_literal_sql(value, coltype)
    except _ExpressionError as e:
        raise AnalysisError(str(e)) from e


def _compile_predicates(conds: list[FilterCond], columns: list[str], types: dict[str, str], context: str, inline_values: bool = False) -> tuple[str, list]:
    """Render FilterCond list to `AND`-joined clauses (no leading keyword) —
    shared by Filter/Having/Qualify, which differ only in which SQL keyword
    (`WHERE`/`HAVING`/`QUALIFY`) and which column set they validate against."""
    if not conds:
        return "", []
    clauses: list[str] = []
    params: list = []
    for f in conds:
        _validate_column(f.column, columns, context)
        op_key = f.operator.lower()
        sop = _OP_SQL.get(op_key)
        if not sop:
            raise AnalysisError(f"unknown {context} operator: {f.operator}")
        qcol = _ident(f.column)
        if op_key in VALUELESS_OPS:
            clauses.append(f"{qcol} {sop}")
        elif sop == "LIKE":
            if inline_values:
                clauses.append(f"CAST({qcol} AS VARCHAR) LIKE {_sql_literal(f'%{f.value}%', 'VARCHAR')}")
            else:
                clauses.append(f"CAST({qcol} AS VARCHAR) LIKE ?")
                params.append(f"%{f.value}%")
        else:
            coltype = types.get(f.column, "VARCHAR")
            if inline_values:
                clauses.append(f"{qcol} {sop} CAST({_sql_literal(f.value, coltype)} AS {coltype})")
            else:
                clauses.append(f"{qcol} {sop} CAST(? AS {coltype})")
                params.append(f.value)
    return " AND ".join(clauses), params


def _compile_filters(filters: list[FilterCond], columns: list[str], types: dict[str, str], inline_values: bool = False) -> tuple[str, list]:
    clause, params = _compile_predicates(filters, columns, types, "filter", inline_values)
    return (f" WHERE {clause}", params) if clause else ("", [])


def _compile_window_expr(w: WindowExpr, columns: list[str], inline_values: bool = False) -> tuple[str, list]:
    from .expression import FUNCTIONS

    spec = FUNCTIONS.get(w.function_id)
    if spec is None or spec.category not in ("aggregate", "window"):
        raise AnalysisError(f"function not allowed in a window expression: {w.function_id}")
    args_sql: list[str] = []
    params: list = []
    for a in w.args:
        try:
            frag, p = compile_expression(a, columns, allow_aggregate=False, allow_window=False, inline_values=inline_values)
        except _ExpressionError as e:
            raise AnalysisError(str(e)) from e
        args_sql.append(frag)
        params.extend(p)
    for c in w.partition_by:
        _validate_column(c, columns, "window partition_by")
    for o in w.order_by:
        _validate_column(o.column, columns, "window order_by")

    over_parts: list[str] = []
    if w.partition_by:
        over_parts.append("PARTITION BY " + ", ".join(_ident(c) for c in w.partition_by))
    if w.order_by:
        terms = [f"{_ident(o.column)} {'DESC' if o.direction == 'desc' else 'ASC'}" for o in w.order_by]
        over_parts.append("ORDER BY " + ", ".join(terms))
    if w.frame is not None:
        unit = w.frame.unit.upper()
        start = "UNBOUNDED PRECEDING" if w.frame.preceding is None else f"{int(w.frame.preceding)} PRECEDING"
        end = "CURRENT ROW" if w.frame.following == 0 else f"{int(w.frame.following)} FOLLOWING"
        over_parts.append(f"{unit} BETWEEN {start} AND {end}")
    over_sql = " ".join(over_parts)
    frag = f"{spec.sql_name}({', '.join(args_sql)}) OVER ({over_sql})"
    return frag, params


def compile_analysis(spec: AnalysisSpec, relation: str, columns: list[str], column_types: dict[str, str] | None = None, inline_values: bool = False, local_sources: dict | None = None) -> CompiledAnalysis:
    """Compile an AnalysisSpec into SQL over `relation` (§10.1, §42) — a
    multi-stage CTE pipeline when the spec needs one (Derive -> Filter ->
    Aggregate/Having -> Window -> Qualify/Distinct/Order/Limit), collapsing
    to a single SELECT for the common simple cases exactly as before.
    `inline_values=True` produces a standalone SQL string with literal
    values instead of `?` placeholders — required when the SQL will be saved
    as a durable step (P11.16): a saved step's SQL text is re-validated and
    re-run on every pipeline compile with no separate params list attached.

    Raises AnalysisError on any column/operator/aggregation/function it
    can't resolve — never silently drops a clause."""
    types = dict(column_types or {})
    cur = relation
    cur_columns = list(columns)
    all_params: list = []
    ctes: list[tuple[str, str]] = []
    stage_idx = 0

    def stage(cte_sql: str) -> str:
        nonlocal stage_idx, cur
        stage_idx += 1
        name = f"_s{stage_idx}"
        ctes.append((name, cte_sql))
        cur = name
        return name

    # 0. Join — first stage; changes the row/column set before anything else
    # sees it (§8.5). The right-side relation is resolved by the caller
    # (WorkspaceSession) against the shared source registry — this operator
    # itself never receives a filesystem path.
    if spec.join:
        j = spec.join
        lsrc = dict(local_sources or {})
        src = lsrc.get(j.local_source_id)
        if src is None:
            raise AnalysisError(f"join references an unknown source: {j.local_source_id}")
        jt = j.join_type.lower()
        if jt not in JOIN_TYPES:
            raise AnalysisError(f"unknown join type: {j.join_type}")
        if not j.keys:
            raise AnalysisError("join needs at least one key")
        for lk, rk in j.keys:
            _validate_column(lk, cur_columns, "join left key")
            if rk not in src["columns"]:
                raise AnalysisError(f"join right key not found: {rk}")
        on = " AND ".join(f"l.{_ident(lk)} = r.{_ident(rk)}" for lk, rk in j.keys)
        if jt in ("semi", "anti"):
            proj = "l.*"
            new_columns = list(cur_columns)
        else:
            right_keys = {rk for _, rk in j.keys}
            add_cols: list[tuple[str, str]] = []
            for c in src["columns"]:
                if c in right_keys:
                    continue
                # collision_policy: suffix_right (§8.5's field) — never
                # silently drop a right-side column just because a same-named
                # left column exists.
                name = f"{c}_right" if c in cur_columns else c
                add_cols.append((c, name))
            proj = ", ".join(["l.*"] + [f"r.{_ident(c)} AS {_ident(name)}" for c, name in add_cols])
            new_columns = cur_columns + [name for _, name in add_cols]
        stage(f"SELECT {proj} FROM {cur} AS l {JOIN_TYPES[jt]} {src['rel']} AS r ON {on}")
        cur_columns = new_columns

    # 1. Derive — row-level computed columns, available to everything below.
    if spec.derive:
        rendered: list[tuple[str, str]] = []
        for d in spec.derive:
            if d.name in cur_columns:
                raise AnalysisError(f"derive column name collides with an existing column: {d.name}")
            try:
                frag, params = compile_expression(d.expr, cur_columns, allow_aggregate=False, allow_window=False, inline_values=inline_values)
            except _ExpressionError as e:
                raise AnalysisError(str(e)) from e
            rendered.append((d.name, frag))
            all_params.extend(params)
            cur_columns = cur_columns + [d.name]
            types[d.name] = d.result_type
        proj = ", ".join(f"{frag} AS {_ident(name)}" for name, frag in rendered)
        stage(f"SELECT *, {proj} FROM {cur}")

    # 2. Filter — pre-aggregation WHERE.
    where_sql, where_params = _compile_filters(spec.filters, cur_columns, types, inline_values)
    if where_sql:
        all_params.extend(where_params)
        stage(f"SELECT * FROM {cur}{where_sql}")

    for d in spec.dimensions:
        _validate_column(d, cur_columns, "dimension")
    for m in spec.measures:
        if m.aggregation != "count" and m.aggregation not in AGGREGATIONS:
            raise AnalysisError(f"unknown aggregation: {m.aggregation}")
        if m.column != "*":
            _validate_column(m.column, cur_columns, "measure")
    if spec.having and not (spec.dimensions or spec.measures):
        raise AnalysisError("having requires an aggregate (dimensions/measures)")

    is_raw_rows = not spec.dimensions and not spec.measures
    is_aggregate = not spec.dimensions and len(spec.measures) == 1 and not spec.window

    # 3. Aggregate (+ Having) — collapses rows by group.
    if is_raw_rows:
        output_columns = list(cur_columns)
    else:
        proj = [_ident(d) for d in spec.dimensions]
        output_columns = list(spec.dimensions)
        for m in spec.measures:
            fn = AGGREGATIONS.get(m.aggregation, "COUNT")
            distinct = "DISTINCT " if m.aggregation == "count_distinct" else ""
            arg = "*" if m.column == "*" else _ident(m.column)
            name = m.output_name()
            proj.append(f"{fn}({distinct}{arg}) AS {_ident(name)}")
            output_columns.append(name)
            types[name] = "BIGINT" if m.aggregation in ("count", "count_distinct") else "DOUBLE"
        select_list = ", ".join(proj) if proj else "*"
        group_by = f" GROUP BY {', '.join(_ident(d) for d in spec.dimensions)}" if spec.dimensions else ""
        having_sql, having_params = _compile_predicates(spec.having, output_columns, types, "having", inline_values)
        all_params.extend(having_params)
        having_clause = f" HAVING {having_sql}" if having_sql else ""
        stage(f"SELECT {select_list} FROM {cur}{group_by}{having_clause}")
        cur_columns = output_columns

    # 4. Window — adds columns via OVER(...); never removes rows. Combined
    # directly into the final SELECT below rather than its own CTE stage:
    # DuckDB requires QUALIFY (and, empirically, the window expression it
    # references) to live in the same SELECT block, not a wrapping query
    # over a CTE that merely re-exposes the window column via `SELECT *`.
    window_proj = ""
    if spec.window:
        window_parts = []
        for w in spec.window:
            frag, wparams = _compile_window_expr(w, cur_columns, inline_values)
            all_params.extend(wparams)
            window_parts.append(f"{frag} AS {_ident(w.alias)}")
            types[w.alias] = "BIGINT" if w.function_id in ("rank", "row_number") else "DOUBLE"
        window_proj = ", " + ", ".join(window_parts)
        window_aliases = [w.alias for w in spec.window]
        cur_columns = cur_columns + window_aliases
        output_columns = output_columns + window_aliases

    # 4b. Window-derive — computed columns that may reference window-alias
    # outputs (e.g. period-over-period delta = value - LAG(value)). Appended
    # to the SAME final SELECT list as the window expressions: DuckDB
    # supports lateral column aliasing, so a later SELECT-list expression can
    # reference an earlier one's alias in the same query block (verified).
    if spec.window_derive:
        if not spec.window:
            raise AnalysisError("window_derive requires at least one window expression")
        wd_parts = []
        for d in spec.window_derive:
            if d.name in cur_columns:
                raise AnalysisError(f"window_derive column name collides with an existing column: {d.name}")
            try:
                frag, dparams = compile_expression(d.expr, cur_columns, allow_aggregate=False, allow_window=False, inline_values=inline_values)
            except _ExpressionError as e:
                raise AnalysisError(str(e)) from e
            all_params.extend(dparams)
            wd_parts.append(f"{frag} AS {_ident(d.name)}")
            cur_columns = cur_columns + [d.name]
            output_columns = output_columns + [d.name]
            types[d.name] = d.result_type
        window_proj += ", " + ", ".join(wd_parts)

    # 5. Qualify — filters on window outputs.
    if spec.qualify and not spec.window:
        raise AnalysisError("qualify requires at least one window expression")
    qualify_sql, qualify_params = _compile_predicates(spec.qualify, cur_columns, types, "qualify", inline_values)
    all_params.extend(qualify_params)
    qualify_clause = f" QUALIFY {qualify_sql}" if qualify_sql else ""

    # 6. Distinct / Order / Limit.
    distinct_kw = "DISTINCT " if spec.distinct else ""
    order_sql = ""
    if spec.sort:
        terms = []
        for s in spec.sort:
            _validate_column(s.column, output_columns, "sort")
            direction = "DESC" if s.direction == "desc" else "ASC"
            terms.append(f"{_ident(s.column)} {direction}")
        order_sql = " ORDER BY " + ", ".join(terms)

    limit = max(1, min(int(spec.limit or 500), 10_000))
    final_select = f"SELECT {distinct_kw}*{window_proj} FROM {cur}{qualify_clause}{order_sql} LIMIT {limit}"
    cte_defs = [f"{n} AS ({s})" for n, s in ctes]
    sql = ("WITH " + ", ".join(cte_defs) + " " + final_select) if cte_defs else final_select
    return CompiledAnalysis(
        sql=sql, params=all_params, output_columns=output_columns, is_raw_rows=is_raw_rows,
        is_aggregate=is_aggregate, cte_defs=cte_defs, final_select=final_select,
    )


def visualization_hint(spec: AnalysisSpec, compiled: CompiledAnalysis, column_types: dict[str, str] | None = None) -> str:
    """Deterministic visualization selection — no AI (§23)."""
    if compiled.is_raw_rows:
        return "grid"
    if compiled.is_aggregate:
        return "kpi"
    if len(spec.dimensions) == 1 and len(spec.measures) == 1:
        types = dict(column_types or {})
        dim_type = types.get(spec.dimensions[0], "").upper()
        if dim_type in TEMPORAL_TYPES:
            return "line"
        return "bar"
    return "table"


def default_step_name(spec: AnalysisSpec) -> str:
    """Human-readable name for a promoted step (§28) — e.g. "avg claim_amount
    by gender" rather than exposing sql_transform/AnalysisSpec internals."""
    parts: list[str] = []
    if spec.filters:
        f = spec.filters[0]
        value = "" if f.operator in ("is_null", "is_not_null") else f" {f.value}"
        parts.append(f"{f.column} {f.operator}{value}".strip())
        if len(spec.filters) > 1:
            parts[-1] += f" (+{len(spec.filters) - 1} more)"
    if spec.measures:
        m = spec.measures[0]
        measure_desc = f"{m.aggregation} {m.column}" if m.column != "*" else m.aggregation
        if spec.dimensions:
            parts.append(f"{measure_desc} by {', '.join(spec.dimensions)}")
        else:
            parts.append(measure_desc)
    elif spec.dimensions:
        parts.append(f"group by {', '.join(spec.dimensions)}")
    return " · ".join(parts) or "Analysis"
