import pyarrow as pa
import pytest

from pyjama_backend import ai_sql

SCHEMA = [
    {"name": "customer_id", "type": "BIGINT"},
    {"name": "country", "type": "VARCHAR"},
    {"name": "revenue", "type": "DOUBLE"},
    {"name": "cost", "type": "DOUBLE"},
]

SECURITY_CORPUS = [
    "ATTACH 'x.db' AS x; SELECT * FROM x.t",
    "SELECT * FROM read_parquet('https://evil.com/x.parquet')",
    "INSTALL httpfs",
    "LOAD httpfs",
    "COPY current TO '/tmp/results.csv'",
    "EXPORT DATABASE '/tmp/dump'",
    "DELETE FROM current WHERE revenue = 0",
    "UPDATE current SET revenue = 0",
    "INSERT INTO current VALUES (1, 'x', 1, 1)",
    "CREATE TABLE evil AS SELECT * FROM current",
    "DROP TABLE current",
    "SELECT * FROM information_schema.tables",
    "PRAGMA database_list",
    "SELECT * FROM current; DROP TABLE current",
    "SELECT * FROM other_table",
]


@pytest.mark.parametrize("sql", SECURITY_CORPUS)
def test_security_corpus_blocked(sql):
    with pytest.raises(ai_sql.SqlPolicyError):
        ai_sql.validate_sql(sql)


def test_allows_select_and_cte():
    v = ai_sql.validate_sql("SELECT country, AVG(revenue - cost) AS avg_margin FROM current GROUP BY country")
    assert "current" in v.lower()
    v2 = ai_sql.validate_sql("WITH t AS (SELECT * FROM current WHERE revenue > 0) SELECT country FROM t")
    assert v2


def test_strips_markdown_fences():
    v = ai_sql.validate_sql("```sql\nSELECT * FROM current\n```")
    assert v.strip()


def test_rejects_empty():
    with pytest.raises(ai_sql.SqlPolicyError):
        ai_sql.validate_sql("   ")


def test_restricted_connection_only_sees_current():
    table = pa.table({"country": ["NL", "DE"], "revenue": [10.0, 20.0]})
    con = ai_sql.restricted_connection(table)
    try:
        rows = con.execute("SELECT count(*) FROM current").fetchone()
        assert rows[0] == 2
        with pytest.raises(Exception):
            con.execute("SELECT * FROM read_parquet('/etc/passwd')")
    finally:
        con.close()


def test_explain_and_execute_adds_limit():
    table = pa.table({"n": list(range(20))})
    con = ai_sql.restricted_connection(table)
    try:
        res = ai_sql.explain_and_execute(con, "SELECT * FROM current", row_cap=5)
        assert res.num_rows == 6  # cap+1, so caller can detect truncation
    finally:
        con.close()


def test_explain_and_execute_raises_on_binder_error():
    table = pa.table({"n": [1, 2, 3]})
    con = ai_sql.restricted_connection(table)
    try:
        with pytest.raises(Exception):
            ai_sql.explain_and_execute(con, "SELECT missing_col FROM current")
    finally:
        con.close()


def test_build_prompt_deterministic():
    p1 = ai_sql.build_prompt(SCHEMA, "top countries by margin")
    p2 = ai_sql.build_prompt(SCHEMA, "top countries by margin")
    assert p1 == p2
    assert "current(" in p1[0]["content"]


def test_build_prompt_without_sample_has_no_data_values():
    table = pa.table({"country": ["SECRET_NL"], "revenue": [123456.0]})
    block = ""  # sampling disabled
    p = ai_sql.build_prompt(SCHEMA, "top countries", block)
    assert "SECRET_NL" not in p[0]["content"]
    assert "123456" not in p[0]["content"]


def test_sample_rows_block_bounded_regardless_of_table_size():
    # A big, wide table: sampling must stay tiny no matter what.
    n = 50_000
    table = pa.table({
        "country": [f"country_{i}" for i in range(n)],
        "note": ["x" * 500 for _ in range(n)],  # oversized cell content
    })
    block = ai_sql.sample_rows_block(table)
    assert block.count("\n") <= ai_sql.MAX_SAMPLE_ROWS  # header + <=5 data rows
    assert len(block) <= ai_sql.MAX_SAMPLE_BLOCK_CHARS + len("\n…(truncated)")
    assert "x" * 500 not in block  # oversized cell was truncated


def test_sample_rows_block_empty_table():
    table = pa.table({"a": pa.array([], type=pa.int64())})
    assert ai_sql.sample_rows_block(table) == ""


def test_sample_rows_block_included_in_prompt():
    table = pa.table({"country": ["NL", "DE"], "revenue": [10.0, 20.0]})
    block = ai_sql.sample_rows_block(table)
    p = ai_sql.build_prompt(SCHEMA, "top countries", block)
    assert "NL" in p[0]["content"] and "DE" in p[0]["content"]
    assert "for value/format reference only" in p[0]["content"]
