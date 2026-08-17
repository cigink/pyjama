"""Databricks SQL generation — the injection boundary (IMPLEMENTATION_PLAN §8.3).

Rules:
  1. Identifiers (catalog/schema/table/column) come from metadata and are quoted
     with a backtick encoder that doubles embedded backticks. Never interpolated
     from free text.
  2. Values are never string-formatted into SQL. They become named statement
     parameters (:p0, :p1, ...) sent separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SqlError(Exception):
    pass


def quote_ident(ident: str) -> str:
    if not ident:
        raise SqlError("empty identifier")
    if any(ord(c) < 0x20 for c in ident):
        raise SqlError(f"invalid identifier: {ident!r}")
    return "`" + ident.replace("`", "``") + "`"


def quote_qualified(full_name: str) -> str:
    return ".".join(quote_ident(part) for part in full_name.split("."))


class FilterOp(Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    LT = "lt"
    BEFORE = "before"
    AFTER = "after"
    CONTAINS = "contains"
    IN_LIST = "in_list"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"

    @classmethod
    def parse(cls, s: str) -> "FilterOp":
        table = {
            "eq": cls.EQ, "equals": cls.EQ, "=": cls.EQ,
            "ne": cls.NE, "not equals": cls.NE, "!=": cls.NE,
            "gt": cls.GT, "greater than": cls.GT, ">": cls.GT,
            "lt": cls.LT, "less than": cls.LT, "<": cls.LT,
            "before": cls.BEFORE, "after": cls.AFTER,
            "contains": cls.CONTAINS,
            "in_list": cls.IN_LIST, "in list": cls.IN_LIST,
            "is_null": cls.IS_NULL, "is null": cls.IS_NULL,
            "is_not_null": cls.IS_NOT_NULL, "is not null": cls.IS_NOT_NULL,
        }
        if s not in table:
            raise SqlError(f"unknown operator: {s}")
        return table[s]


@dataclass
class Predicate:
    column: str
    op: FilterOp
    value: str = ""


@dataclass
class StatementParam:
    name: str
    value: str
    type: str = "STRING"

    def to_api(self) -> dict:
        return {"name": self.name, "value": self.value, "type": self.type}


@dataclass
class CompiledQuery:
    sql: str
    params: list[StatementParam] = field(default_factory=list)

    def params_api(self) -> list[dict]:
        return [p.to_api() for p in self.params]


def build_working_set_select(table: str, columns: list[str], predicates: list[Predicate]) -> CompiledQuery:
    projection = "*" if not columns else ", ".join(quote_ident(c) for c in columns)
    sql = f"SELECT {projection} FROM {quote_qualified(table)}"
    params: list[StatementParam] = []

    if predicates:
        clauses: list[str] = []
        for i, p in enumerate(predicates):
            col = quote_ident(p.column)
            pname = f"p{i}"
            if p.op is FilterOp.IS_NULL:
                clauses.append(f"{col} IS NULL")
            elif p.op is FilterOp.IS_NOT_NULL:
                clauses.append(f"{col} IS NOT NULL")
            elif p.op is FilterOp.CONTAINS:
                clauses.append(f"{col} LIKE :{pname}")
                params.append(StatementParam(pname, f"%{p.value}%"))
            elif p.op is FilterOp.IN_LIST:
                items = [x.strip() for x in p.value.split(",") if x.strip()]
                if not items:
                    raise SqlError("IN list has no values")
                placeholders = []
                for j, item in enumerate(items):
                    n = f"{pname}_{j}"
                    placeholders.append(f":{n}")
                    params.append(StatementParam(n, item))
                clauses.append(f"{col} IN ({', '.join(placeholders)})")
            else:
                sql_op = {
                    FilterOp.EQ: "=", FilterOp.NE: "!=",
                    FilterOp.GT: ">", FilterOp.AFTER: ">",
                    FilterOp.LT: "<", FilterOp.BEFORE: "<",
                }[p.op]
                clauses.append(f"{col} {sql_op} :{pname}")
                params.append(StatementParam(pname, p.value))
        sql += " WHERE " + " AND ".join(clauses)

    return CompiledQuery(sql=sql, params=params)
