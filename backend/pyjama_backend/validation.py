"""Validation rule compiler (Phase 6).

Rules compile to DuckDB boolean "pass" expressions over the final pipeline
output. Injection-safe: columns are validated + double-quoted, values are bound.
`error` rules block commit; `warning` rules are reviewable but non-blocking
(IMPLEMENTATION_PLAN §14).
"""

from __future__ import annotations


class ValidationError(Exception):
    pass


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def compile_rule(rule: dict, columns: set[str], types: dict[str, str]) -> tuple[str, list]:
    """Return (pass_expr_sql, params). A row passes when the expr is true."""
    col = rule.get("column")
    if col not in columns:
        raise ValidationError(f"rule references unknown column: {col}")
    qcol = _ident(col)
    coltype = types.get(col, "VARCHAR")
    kind = rule.get("kind")

    if kind == "not_null":
        return f"{qcol} IS NOT NULL", []
    if kind == "not_empty":
        return f"({qcol} IS NOT NULL AND CAST({qcol} AS VARCHAR) <> '')", []
    if kind == "contains":
        return f"CAST({qcol} AS VARCHAR) LIKE ?", [f"%{rule.get('value', '')}%"]
    if kind in ("gt", "lt", "gte", "lte", "eq", "ne"):
        op = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<=", "eq": "=", "ne": "!="}[kind]
        return f"{qcol} {op} CAST(? AS {coltype})", [rule.get("value", "")]
    if kind == "in_list":
        items = [x.strip() for x in str(rule.get("value", "")).split(",") if x.strip()]
        if not items:
            raise ValidationError("in_list rule has no values")
        ph = ", ".join(f"CAST(? AS {coltype})" for _ in items)
        return f"{qcol} IN ({ph})", items
    raise ValidationError(f"unknown rule kind: {kind}")
