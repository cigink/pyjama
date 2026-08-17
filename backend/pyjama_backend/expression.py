"""Canonical Expression AST (Phase 12, P12.1-P12.2 — DuckDB-Native Analytical
Engine §6, §6.1).

Structured expressions instead of free-form SQL text, so the same
representation can be built by direct manipulation, the visual builder, and
AI, and compiled safely by one code path (§2.1 "one canonical IR"). This is
the ephemeral-Explore counterpart to `formula.py`'s text-grammar compiler,
which remains the representation for the durable `formula` pipeline step —
not forked or replaced here, just given a structured sibling.

Every node compiles to a parameterized SQL fragment: identifiers are
resolved against a real column set and double-quoted; literal values are
bound as `?` params, never interpolated.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ExpressionError(Exception):
    pass


NUMERIC_TYPES = {"BIGINT", "DOUBLE"}


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def literal_sql(value: object, coltype: str) -> str:
    """Safely inline a value as a SQL literal — for standalone/promoted SQL
    strings that must carry no separate params list (shared with
    analysis_spec.py's promotion path, which has the exact same requirement)."""
    if value is None:
        return "NULL"
    if coltype in NUMERIC_TYPES:
        try:
            return repr(float(value)) if "." in str(value) else str(int(value))
        except (TypeError, ValueError):
            raise ExpressionError(f"expected a numeric value, got: {value!r}")
    if coltype == "BOOLEAN":
        return "TRUE" if str(value).lower() in ("true", "1") else "FALSE"
    text = str(value).replace("'", "''")
    return f"'{text}'"


# ---- Function registry (§6.1) — the only path from a function name to SQL.
# Categories gate where a function may be used: row-level Derive contexts
# reject `aggregate`/`window`; those are only valid inside the Aggregate and
# Window operators respectively (§8.6's explicit rule).

@dataclass(frozen=True)
class FunctionSpec:
    sql_name: str
    category: str  # scalar_numeric | scalar_text | date_time | scalar_generic | aggregate | window
    min_args: int = 1
    max_args: int | None = None


FUNCTIONS: dict[str, FunctionSpec] = {
    # scalar_numeric
    "round": FunctionSpec("round", "scalar_numeric", 1, 2),
    "abs": FunctionSpec("abs", "scalar_numeric", 1, 1),
    "floor": FunctionSpec("floor", "scalar_numeric", 1, 1),
    "ceil": FunctionSpec("ceil", "scalar_numeric", 1, 1),
    # scalar_text
    "upper": FunctionSpec("upper", "scalar_text", 1, 1),
    "lower": FunctionSpec("lower", "scalar_text", 1, 1),
    "trim": FunctionSpec("trim", "scalar_text", 1, 1),
    "length": FunctionSpec("length", "scalar_text", 1, 1),
    "concat": FunctionSpec("concat", "scalar_text", 1, None),
    "substr": FunctionSpec("substr", "scalar_text", 2, 3),
    "replace": FunctionSpec("replace", "scalar_text", 3, 3),
    # date_time
    "date_trunc": FunctionSpec("date_trunc", "date_time", 2, 2),
    "date_part": FunctionSpec("date_part", "date_time", 2, 2),
    # scalar_generic
    "coalesce": FunctionSpec("coalesce", "scalar_generic", 1, None),
    "nullif": FunctionSpec("nullif", "scalar_generic", 2, 2),
    # aggregate — Aggregate/Window operator contexts only, never row-level Derive.
    "sum": FunctionSpec("sum", "aggregate", 1, 1),
    "avg": FunctionSpec("avg", "aggregate", 1, 1),
    "count": FunctionSpec("count", "aggregate", 0, 1),
    "min": FunctionSpec("min", "aggregate", 1, 1),
    "max": FunctionSpec("max", "aggregate", 1, 1),
    "median": FunctionSpec("median", "aggregate", 1, 1),
    "stddev": FunctionSpec("stddev", "aggregate", 1, 1),
    "quantile_cont": FunctionSpec("quantile_cont", "aggregate", 2, 2),
    # window — Window operator only.
    "rank": FunctionSpec("rank", "window", 0, 0),
    "row_number": FunctionSpec("row_number", "window", 0, 0),
    "lag": FunctionSpec("lag", "window", 1, 3),
    "lead": FunctionSpec("lead", "window", 1, 3),
    "first_value": FunctionSpec("first_value", "window", 1, 1),
    "last_value": FunctionSpec("last_value", "window", 1, 1),
}

# Blocked categories, enumerated for clarity even though FUNCTIONS never
# contains them — mirrors ai_sql.BLOCKED_FUNCTIONS's spirit for row-level
# expression context (§6.1's external_io / side_effect rows: never accepted
# from a visual plan or AI at all, at any layer).
BLOCKED_CATEGORIES = {"external_io", "side_effect"}

_BINARY_OPS = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/", "concat": "||",
               "eq": "=", "ne": "!=", "gt": ">", "lt": "<", "gte": ">=", "lte": "<=",
               "and": "AND", "or": "OR"}
_UNARY_OPS = {"neg": "-", "not": "NOT "}


# ---- AST ----

@dataclass
class ColumnRef:
    column: str


@dataclass
class Literal:
    value: object
    type: str = "VARCHAR"


@dataclass
class Unary:
    op: str
    expr: "Expression"


@dataclass
class Binary:
    op: str
    left: "Expression"
    right: "Expression"


@dataclass
class FunctionCall:
    function_id: str
    args: list["Expression"] = field(default_factory=list)


@dataclass
class CaseWhen:
    branches: list[tuple["Expression", "Expression"]]
    else_expr: "Expression | None" = None


@dataclass
class Cast:
    expr: "Expression"
    target_type: str


@dataclass
class InList:
    expr: "Expression"
    values: list["Expression"]
    negated: bool = False


@dataclass
class Between:
    expr: "Expression"
    lower: "Expression"
    upper: "Expression"
    negated: bool = False


@dataclass
class IsNull:
    expr: "Expression"
    negated: bool = False


@dataclass
class Like:
    expr: "Expression"
    pattern: "Expression"
    case_sensitive: bool = True


Expression = ColumnRef | Literal | Unary | Binary | FunctionCall | CaseWhen | Cast | InList | Between | IsNull | Like

_NODE_TYPES = {
    "column_ref": ColumnRef, "literal": Literal, "unary": Unary, "binary": Binary,
    "function_call": FunctionCall, "case_when": CaseWhen, "cast": Cast,
    "in_list": InList, "between": Between, "is_null": IsNull, "like": Like,
}


def expr_from_dict(d: dict) -> Expression:
    """Deserialize a wire-format expression dict into the AST (recursive)."""
    if not isinstance(d, dict) or "type" not in d:
        raise ExpressionError(f"malformed expression node: {d!r}")
    kind = d["type"]
    if kind == "column_ref":
        return ColumnRef(column=d["column"])
    if kind == "literal":
        return Literal(value=d.get("value"), type=d.get("value_type", "VARCHAR"))
    if kind == "unary":
        return Unary(op=d["op"], expr=expr_from_dict(d["expr"]))
    if kind == "binary":
        return Binary(op=d["op"], left=expr_from_dict(d["left"]), right=expr_from_dict(d["right"]))
    if kind == "function_call":
        return FunctionCall(function_id=d["function_id"], args=[expr_from_dict(a) for a in d.get("args", [])])
    if kind == "case_when":
        branches = [(expr_from_dict(c), expr_from_dict(v)) for c, v in d["branches"]]
        else_expr = expr_from_dict(d["else_expr"]) if d.get("else_expr") is not None else None
        return CaseWhen(branches=branches, else_expr=else_expr)
    if kind == "cast":
        return Cast(expr=expr_from_dict(d["expr"]), target_type=d["target_type"])
    if kind == "in_list":
        return InList(expr=expr_from_dict(d["expr"]), values=[expr_from_dict(v) for v in d["values"]], negated=d.get("negated", False))
    if kind == "between":
        return Between(expr=expr_from_dict(d["expr"]), lower=expr_from_dict(d["lower"]), upper=expr_from_dict(d["upper"]), negated=d.get("negated", False))
    if kind == "is_null":
        return IsNull(expr=expr_from_dict(d["expr"]), negated=d.get("negated", False))
    if kind == "like":
        return Like(expr=expr_from_dict(d["expr"]), pattern=expr_from_dict(d["pattern"]), case_sensitive=d.get("case_sensitive", True))
    raise ExpressionError(f"unknown expression node type: {kind}")


def compile_expression(expr: Expression, columns: list[str], allow_aggregate: bool = False, allow_window: bool = False, inline_values: bool = False) -> tuple[str, list]:
    """Compile an Expression to a SQL fragment. Raises ExpressionError on any
    unknown column/function or a function used outside its allowed context
    (row-level Derive never gets aggregate/window). `inline_values=True`
    renders Literal nodes as inline SQL literals instead of `?` params — for
    standalone SQL that must carry no separate params list (a promoted step)."""
    params: list = []

    def go(node: Expression) -> str:
        if isinstance(node, ColumnRef):
            if node.column not in columns:
                raise ExpressionError(f"unknown column: {node.column}")
            return _ident(node.column)
        if isinstance(node, Literal):
            if inline_values:
                return literal_sql(node.value, node.type)
            params.append(node.value)
            return "?"
        if isinstance(node, Unary):
            sop = _UNARY_OPS.get(node.op)
            if not sop:
                raise ExpressionError(f"unknown unary operator: {node.op}")
            return f"({sop}{go(node.expr)})"
        if isinstance(node, Binary):
            sop = _BINARY_OPS.get(node.op)
            if not sop:
                raise ExpressionError(f"unknown binary operator: {node.op}")
            return f"({go(node.left)} {sop} {go(node.right)})"
        if isinstance(node, FunctionCall):
            spec = FUNCTIONS.get(node.function_id)
            if spec is None or spec.category in BLOCKED_CATEGORIES:
                raise ExpressionError(f"function not allowed: {node.function_id}")
            if spec.category == "aggregate" and not allow_aggregate:
                raise ExpressionError(f"{node.function_id} is an aggregate function — not allowed in a row-level expression")
            if spec.category == "window" and not allow_window:
                raise ExpressionError(f"{node.function_id} is a window function — only allowed inside a Window operator")
            n = len(node.args)
            if n < spec.min_args or (spec.max_args is not None and n > spec.max_args):
                raise ExpressionError(f"{node.function_id} takes {spec.min_args}-{spec.max_args if spec.max_args is not None else 'inf'} args, got {n}")
            args_sql = ", ".join(go(a) for a in node.args)
            return f"{spec.sql_name}({args_sql})"
        if isinstance(node, CaseWhen):
            parts = ["CASE"]
            for cond, val in node.branches:
                parts.append(f"WHEN {go(cond)} THEN {go(val)}")
            if node.else_expr is not None:
                parts.append(f"ELSE {go(node.else_expr)}")
            parts.append("END")
            return " ".join(parts)
        if isinstance(node, Cast):
            return f"CAST({go(node.expr)} AS {node.target_type})"
        if isinstance(node, InList):
            vals_sql = ", ".join(go(v) for v in node.values)
            neg = "NOT " if node.negated else ""
            return f"{go(node.expr)} {neg}IN ({vals_sql})"
        if isinstance(node, Between):
            neg = "NOT " if node.negated else ""
            return f"{go(node.expr)} {neg}BETWEEN {go(node.lower)} AND {go(node.upper)}"
        if isinstance(node, IsNull):
            return f"{go(node.expr)} IS {'NOT ' if node.negated else ''}NULL"
        if isinstance(node, Like):
            return f"{go(node.expr)} {'LIKE' if node.case_sensitive else 'ILIKE'} {go(node.pattern)}"
        raise ExpressionError(f"unknown expression node: {node!r}")

    sql = go(expr)
    return sql, params
