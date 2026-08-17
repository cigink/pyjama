"""Diff engine (Phase 6 — IMPLEMENTATION_PLAN §13).

Compares BASE (the immutable checked-out source, relation `ws`) against FINAL
(the compiled pipeline output), on the source columns that survive to the output,
keyed by the chosen row identifier:

  added     = keys in FINAL not in BASE
  deleted   = keys in BASE not in FINAL
  modified  = keys in both where any non-key source column differs
  unchanged = keys in both, all equal

Builds SQL over the session's DuckDB relations; the session executes it under its
lock. Pipeline params bind once (FINAL appears once in the WITH clause).
"""

from __future__ import annotations


class DiffError(Exception):
    pass


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def build_diff(with_clause: str, final_rel: str, source_cols: list[str], final_cols: list[str], keys: list[str]) -> dict:
    if not keys:
        raise DiffError("a row key is required to diff")
    shared = [c for c in source_cols if c in final_cols]
    for k in keys:
        if k not in shared:
            raise DiffError(f"row key {k} not present in both source and output")
    non_key = [c for c in shared if c not in keys]

    sel = ", ".join(_ident(c) for c in shared)
    join_on = " AND ".join(f"f.{_ident(k)} = b.{_ident(k)}" for k in keys)
    corr_b = " AND ".join(f"b.{_ident(k)} = f.{_ident(k)}" for k in keys)  # b in subquery, f outer
    corr_f = " AND ".join(f"f.{_ident(k)} = b.{_ident(k)}" for k in keys)  # f in subquery, b outer
    distinct = " OR ".join(f"f.{_ident(c)} IS DISTINCT FROM b.{_ident(c)}" for c in non_key) or "FALSE"

    prefix = f"{with_clause}, b AS (SELECT {sel} FROM ws), f AS (SELECT {sel} FROM {final_rel})"

    counts_sql = (
        f"{prefix} SELECT "
        f"(SELECT count(*) FROM f WHERE NOT EXISTS (SELECT 1 FROM b WHERE {corr_b})) AS added, "
        f"(SELECT count(*) FROM b WHERE NOT EXISTS (SELECT 1 FROM f WHERE {corr_f})) AS deleted, "
        f"(SELECT count(*) FROM f JOIN b ON {join_on} WHERE {distinct}) AS modified, "
        f"(SELECT count(*) FROM f JOIN b ON {join_on} WHERE NOT ({distinct})) AS unchanged"
    )

    key_sel = ", ".join(f"f.{_ident(k)} AS {_ident(k)}" for k in keys)
    ba = ", ".join(f"b.{_ident(c)} AS {_ident(c + '__before')}, f.{_ident(c)} AS {_ident(c + '__after')}" for c in non_key)
    sample_cols = key_sel + ((", " + ba) if ba else "")
    samples_sql = f"{prefix} SELECT {sample_cols} FROM f JOIN b ON {join_on} WHERE {distinct} LIMIT ?"

    return {"counts_sql": counts_sql, "samples_sql": samples_sql, "keys": keys, "non_key": non_key}
