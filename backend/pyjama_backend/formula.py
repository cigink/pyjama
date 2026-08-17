"""Safe formula-expression compiler (Phase 4 — part of the Formula step).

Users type expressions like ``revenue - cost`` or ``coalesce(email, 'n/a')``.
Free text next to a SQL engine is an injection risk, so this does NOT pass the
text through. It tokenizes, validates every token against an allowlist, and
re-emits SQL where:
  - identifiers must be real columns (double-quoted),
  - string literals become bound parameters,
  - only a small set of operators and functions is permitted.

Anything else — semicolons, comments, unknown identifiers, disallowed functions —
is rejected. This supports arithmetic, string concat (``||``), and null handling;
richer conditionals can be added later behind the same validator.
"""

from __future__ import annotations

import re

# Functions users may call. Kept deliberately small; extend consciously.
ALLOWED_FUNCS = {
    "coalesce", "nullif", "upper", "lower", "trim", "length", "concat",
    "round", "abs", "floor", "ceil", "substr", "replace",
}

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<number>\d+\.\d+|\d+)
    | (?P<string>'(?:[^']|'')*')
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<op>\|\||[+\-*/])
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<comma>,)
    """,
    re.VERBOSE,
)


class FormulaError(Exception):
    pass


def _duck_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def compile_formula(expr: str, columns: set[str]) -> tuple[str, list]:
    """Compile a formula expression to (sql_fragment, params). Raises
    FormulaError on anything unsafe or unrecognized."""
    if not expr or not expr.strip():
        raise FormulaError("empty formula")

    out: list[str] = []
    params: list = []
    depth = 0
    pos = 0
    n = len(expr)

    while pos < n:
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            raise FormulaError(f"unexpected character at {pos}: {expr[pos]!r}")
        pos = m.end()
        kind = m.lastgroup
        val = m.group()

        if kind == "ws":
            continue
        if kind == "number":
            out.append(val)
        elif kind == "string":
            # 'it''s' -> it's ; bind as a parameter, never interpolate.
            params.append(val[1:-1].replace("''", "'"))
            out.append("?")
        elif kind == "ident":
            lower = val.lower()
            # function call if immediately followed by '('
            rest = expr[pos:]
            is_call = rest.lstrip().startswith("(")
            if is_call:
                if lower not in ALLOWED_FUNCS:
                    raise FormulaError(f"function not allowed: {val}")
                out.append(lower)
            else:
                if val not in columns:
                    raise FormulaError(f"unknown column: {val}")
                out.append(_duck_ident(val))
        elif kind == "op":
            out.append(f" {val} ")
        elif kind == "lparen":
            depth += 1
            out.append("(")
        elif kind == "rparen":
            depth -= 1
            if depth < 0:
                raise FormulaError("unbalanced parentheses")
            out.append(")")
        elif kind == "comma":
            out.append(", ")

    if depth != 0:
        raise FormulaError("unbalanced parentheses")

    sql = "".join(out).strip()
    if not sql:
        raise FormulaError("empty formula")
    return sql, params
