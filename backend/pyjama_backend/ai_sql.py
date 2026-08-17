"""Prompt construction, SQL policy validation, and the restricted DuckDB
exploration sandbox (Phase 10, P10.6-P10.11).

Treat model output as untrusted input. The AST allowlist is the sole
authoritative security mechanism — nothing here trusts the model to have
"only produced SELECT statements".
"""

from __future__ import annotations

import re

import duckdb
import pyarrow as pa
import sqlglot
from sqlglot import expressions as exp

RELATION_NAME = "current"

# Sample rows are bounded regardless of table size — the model is local so
# there's no egress concern, but a huge/wide table must not blow the prompt's
# token budget (PRD §16.1: prefer <2,000 tokens, hard cap 4,096 context).
MAX_SAMPLE_ROWS = 5
MAX_SAMPLE_CELL_CHARS = 40
MAX_SAMPLE_BLOCK_CHARS = 1200

BLOCKED_FUNCTIONS = {
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
    "read_ndjson", "glob", "sniff_csv", "read_text", "read_blob",
    "pragma_database_list", "pragma_table_info", "shell", "system",
}

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|```$", re.MULTILINE)


class SqlPolicyError(Exception):
    """SQL rejected by the allowlist — blocked, not repaired (P10.9)."""


SYSTEM_PROMPT_TEMPLATE = """You are a SQL translation engine for DuckDB.
Return ONLY one raw SQL query. No Markdown. No explanation.
Rules:
1. Query only the relation `{relation}`.
2. Read-only SQL only.
3. Use SELECT or WITH ... SELECT only.
4. Never use ATTACH, COPY, CREATE, DROP, UPDATE, DELETE, INSERT,
   INSTALL, LOAD, PRAGMA, CALL, EXPORT, IMPORT, or filesystem/network functions.
5. Use DuckDB-compatible SQL.
6. Prefer explicit column names over SELECT * when aggregation is used.
7. If the request cannot be answered from the schema, return:
   SELECT 'UNSUPPORTED_REQUEST' AS _error;
Schema:
{relation}(
{columns}
)"""


def sample_rows_block(table: pa.Table, max_rows: int = MAX_SAMPLE_ROWS) -> str:
    """A small, evenly-spaced sample of rows rendered as a pipe-delimited
    block — bounded to `max_rows` and to a hard character cap no matter how
    large or wide the underlying table is. Evenly spaced (not just the head)
    so a sorted table still shows a representative value range."""
    n = table.num_rows
    if n == 0 or max_rows <= 0:
        return ""
    take = min(max_rows, n)
    if take <= 1 or n <= 1:
        idx = [0]
    else:
        idx = sorted({round(i * (n - 1) / (take - 1)) for i in range(take)})
    sub = table.take(idx)
    cols = sub.column_names
    lines = [" | ".join(cols)]
    for row in range(sub.num_rows):
        vals = []
        for c in cols:
            v = sub.column(c)[row].as_py()
            text = "" if v is None else str(v)
            if len(text) > MAX_SAMPLE_CELL_CHARS:
                text = text[:MAX_SAMPLE_CELL_CHARS] + "…"
            vals.append(text)
        lines.append(" | ".join(vals))
    block = "\n".join(lines)
    if len(block) > MAX_SAMPLE_BLOCK_CHARS:
        block = block[:MAX_SAMPLE_BLOCK_CHARS] + "\n…(truncated)"
    return block


def build_prompt(schema: list[dict], question: str, sample_block: str = "") -> list[dict]:
    """Deterministic prompt from schema + question, optionally with a small
    bounded sample of real rows (P10.6-P10.7). The model is local-only, so
    sample values never leave the device — still bounded to keep the prompt
    small and fast regardless of table size."""
    cols = ",\n".join(f"  {c['name']} {c['type']}" for c in schema)
    system = SYSTEM_PROMPT_TEMPLATE.format(relation=RELATION_NAME, columns=cols)
    if sample_block:
        system += (
            f"\n\nSample rows from `{RELATION_NAME}` (subset, for value/format reference only "
            f"— do not assume these are the only values present):\n{sample_block}"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


def build_repair_prompt(schema: list[dict], question: str, rejected_sql: str, error: str) -> list[dict]:
    """Single execution-guided repair attempt (P10.12)."""
    cols = ", ".join(f"{c['name']} {c['type']}" for c in schema)
    content = (
        "The previous SQL failed in DuckDB.\n"
        "Return ONLY corrected DuckDB SQL.\n\n"
        f"Original question:\n{question}\n\n"
        f"Schema:\n{RELATION_NAME}({cols})\n\n"
        f"Rejected SQL:\n{rejected_sql}\n\n"
        f"DuckDB error:\n{error}"
    )
    return [
        {"role": "system", "content": f"You are a SQL translation engine for DuckDB. Query only `{RELATION_NAME}`. Return ONLY corrected raw SQL, no Markdown."},
        {"role": "user", "content": content},
    ]


def strip_fences(raw: str) -> str:
    return _FENCE_RE.sub("", raw).strip()


def validate_sql(raw_sql: str, extra_relations: set[str] | None = None) -> str:
    """AST allowlist (P10.9). Returns the single validated SQL statement, or
    raises SqlPolicyError. Authoritative — do not trust the model's own
    claim to have produced only a SELECT.

    `extra_relations` allowlists additional relation names beyond `current`
    and same-query CTE aliases — used only by the Explore Join operator
    (Phase 12, P12.10) to approve the specific right-side relation(s) a
    promoted join step legitimately references; empty/None for every other
    caller (the AI ask/repair path never passes this)."""
    sql = strip_fences(raw_sql)
    if not sql:
        raise SqlPolicyError("model returned no SQL")

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as e:  # sqlglot raises its own ParseError subclasses
        raise SqlPolicyError(f"parse error: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlPolicyError("exactly one SQL statement is required")

    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union, exp.Subquery)):
        raise SqlPolicyError(f"statement type not allowed: {type(stmt).__name__}")

    # CTE aliases defined within the same statement are allowed as relations.
    cte_aliases = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    allowed_relations = {RELATION_NAME.lower()} | cte_aliases | {r.lower() for r in (extra_relations or ())}

    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if table.db or table.catalog:
            raise SqlPolicyError(f"qualified/external relation not allowed: {table.sql()}")
        if name not in allowed_relations:
            raise SqlPolicyError(f"relation not allowed: {name}")

    for func in stmt.find_all(exp.Func):
        fname = (func.sql_name() or getattr(func, "name", "") or "").lower()
        if fname in BLOCKED_FUNCTIONS:
            raise SqlPolicyError(f"function not allowed: {fname}")

    for anon in stmt.find_all(exp.Anonymous):
        fname = (anon.name or "").lower()
        if fname in BLOCKED_FUNCTIONS:
            raise SqlPolicyError(f"function not allowed: {fname}")

    # Reject any node type belonging to a mutating/DDL/attach/pragma family —
    # defense in depth in case such a node appears nested (e.g. in a CTE body
    # sqlglot still classifies as a command).
    blocked_types = (
        exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
        exp.Alter, exp.Attach, exp.Pragma, exp.Copy, exp.Command,
    )
    for node in stmt.walk():
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, blocked_types):
            raise SqlPolicyError(f"statement/feature not allowed: {type(n).__name__}")

    return stmt.sql(dialect="duckdb")


def restricted_connection(table: pa.Table) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection that can see exactly one relation — `current` —
    and nothing else: no other workspace tables, no filesystem/network
    extensions, no autoinstall (P10.10)."""
    con = duckdb.connect(":memory:")
    con.execute("SET allow_community_extensions = false")
    con.execute("SET autoinstall_known_extensions = false")
    con.execute("SET autoload_known_extensions = false")
    con.execute("SET enable_external_access = false")
    con.register(RELATION_NAME, table)
    con.execute("SET lock_configuration = true")
    return con


def explain_and_execute(con: duckdb.DuckDBPyConnection, sql: str, row_cap: int = 10_000):
    """EXPLAIN before execute (parser/binder check), then run with a result
    row cap for row-returning queries (P10.11)."""
    con.execute(f"EXPLAIN {sql}")  # raises on binder/parser error
    capped_sql = sql
    if not re.search(r"\blimit\b", sql, re.IGNORECASE):
        capped_sql = f"SELECT * FROM ({sql}) AS _capped LIMIT {row_cap + 1}"
    return con.execute(capped_sql).arrow()
