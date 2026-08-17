import duckdb
import pyarrow as pa
import pytest

from pyjama_backend.formula import FormulaError, compile_formula
from pyjama_backend.pipeline import PipelineError, Step, compile_pipeline

SRC = pa.table({
    "customer_id": [1, 2, 2, 3],
    "country": ["NL", "Netherland", "Netherland", "DE"],
    "email": ["a@x.com", "b@x.com", "b@x.com", ""],
    "revenue": [100, 50, 50, 90],
    "cost": [60, 20, 20, 70],
    "updated_at": ["2026-02-01", "2026-01-01", "2026-03-01", "2026-01-15"],
})
COLS = list(SRC.column_names)


def run(steps, up_to=None):
    con = duckdb.connect()
    con.register("ws", SRC)
    c = compile_pipeline(steps, COLS, up_to=up_to)
    sql = f"{c.with_clause} SELECT * FROM {c.final_rel} ORDER BY 1"
    rel = con.execute(sql, c.params).arrow()
    return c, rel


def src_step():
    return Step(id="s", type="source")


def test_filter():
    steps = [src_step(), Step(id="f", type="filter", config={"conditions": [{"column": "country", "op": "eq", "value": "NL"}]})]
    _, rel = run(steps)
    assert rel.num_rows == 1
    assert rel.column("customer_id").to_pylist() == [1]


def test_select_and_rename():
    steps = [
        src_step(),
        Step(id="sel", type="select_columns", config={"columns": ["customer_id", "revenue"]}),
        Step(id="rn", type="rename", config={"from": "revenue", "to": "amount"}),
    ]
    c, rel = run(steps)
    assert rel.column_names == ["customer_id", "amount"]
    assert c.columns == ["customer_id", "amount"]


def test_formula_derived_column():
    steps = [src_step(), Step(id="fm", type="formula", config={"name": "margin", "expression": "revenue - cost"})]
    c, rel = run(steps)
    assert "margin" in rel.column_names
    row0 = {k: rel.column(k).to_pylist()[0] for k in rel.column_names}
    assert row0["margin"] == row0["revenue"] - row0["cost"]


def test_formula_string_and_null_handling():
    steps = [src_step(), Step(id="fm", type="formula", config={"name": "label", "expression": "coalesce(nullif(email, ''), 'missing')"})]
    _, rel = run(steps)
    labels = rel.column("label").to_pylist()
    assert "missing" in labels  # empty email -> 'missing'


def test_deduplicate_keep_latest():
    steps = [src_step(), Step(id="dd", type="deduplicate", config={"key": "customer_id", "keep": "latest"})]
    _, rel = run(steps)
    ids = rel.column("customer_id").to_pylist()
    assert ids == [1, 2, 3]  # duplicate customer_id=2 collapsed
    # kept the latest updated_at for id 2
    row2 = [i for i, v in enumerate(ids) if v == 2][0]
    assert rel.column("updated_at").to_pylist()[row2] == "2026-03-01"


def test_replace_values():
    steps = [src_step(), Step(id="rp", type="replace", config={"column": "country", "mappings": [{"from": "Netherland", "to": "Netherlands"}, {"from": "NL", "to": "Netherlands"}]})]
    _, rel = run(steps)
    assert set(rel.column("country").to_pylist()) == {"Netherlands", "DE"}


def test_chained_pipeline_and_up_to():
    steps = [
        src_step(),
        Step(id="rp", type="replace", config={"column": "country", "mappings": [{"from": "Netherland", "to": "Netherlands"}, {"from": "NL", "to": "Netherlands"}]}),
        Step(id="dd", type="deduplicate", config={"key": "customer_id", "keep": "latest"}),
        Step(id="fm", type="formula", config={"name": "margin", "expression": "revenue - cost"}),
    ]
    _, rel_full = run(steps)
    assert "margin" in rel_full.column_names and rel_full.num_rows == 3

    # up_to the replace step only: no margin, still 4 rows
    _, rel_partial = run(steps, up_to=1)
    assert "margin" not in rel_partial.column_names
    assert rel_partial.num_rows == 4


def test_filter_value_injection_is_bound():
    steps = [src_step(), Step(id="f", type="filter", config={"conditions": [{"column": "country", "op": "eq", "value": "x'; DROP TABLE ws;--"}]})]
    c, rel = run(steps)  # must not raise; value is a bound param
    assert "DROP TABLE" not in c.with_clause
    assert rel.num_rows == 0


def test_formula_rejects_injection_and_unknowns():
    with pytest.raises(FormulaError):
        compile_formula("revenue; DROP TABLE ws", set(COLS))
    with pytest.raises(FormulaError):
        compile_formula("evil(revenue)", set(COLS))  # function not allowed
    with pytest.raises(FormulaError):
        compile_formula("no_such_col + 1", set(COLS))  # unknown column


def test_join_file():
    con = duckdb.connect()
    con.register("ws", SRC)
    mapping = pa.table({"customer_id": [1, 2, 3], "region": ["EMEA", "APAC", "AMER"]})
    con.register("ls_map", mapping)
    steps = [src_step(), Step(id="j", type="join_file", config={"local_source_id": "map", "join_type": "left", "keys": [{"left": "customer_id", "right": "customer_id"}]})]
    local_sources = {"map": {"rel": "ls_map", "columns": ["customer_id", "region"]}}
    c = compile_pipeline(steps, COLS, local_sources=local_sources)
    rel = con.execute(f"{c.with_clause} SELECT * FROM {c.final_rel} ORDER BY customer_id, region", c.params).arrow()
    assert "region" in rel.column_names
    regions = dict(zip(rel.column("customer_id").to_pylist(), rel.column("region").to_pylist()))
    assert regions[1] == "EMEA" and regions[3] == "AMER"


def test_join_unknown_source_errors():
    with pytest.raises(PipelineError):
        compile_pipeline([src_step(), Step(id="j", type="join_file", config={"local_source_id": "nope", "keys": [{"left": "customer_id", "right": "x"}]})], COLS, local_sources={})


def test_manual_edit_overlay_single_column():
    steps = [
        src_step(),
        Step(id="me", type="manual_edit", config={"keys": ["customer_id"], "edits": [{"key": {"customer_id": 2}, "column": "country", "value": "Netherlands"}]}),
    ]
    _, rel = run(steps)
    countries = dict(zip(rel.column("customer_id").to_pylist(), rel.column("country").to_pylist()))
    assert countries[2] == "Netherlands"  # edited row
    assert countries[1] == "NL"  # untouched row unchanged
    # source partition itself unaffected — SRC table object is untouched
    assert SRC.column("country").to_pylist()[1] == "Netherland"


def test_manual_edit_overlay_multi_column():
    steps = [
        src_step(),
        Step(id="me", type="manual_edit", config={
            "keys": ["customer_id"],
            "edits": [
                {"key": {"customer_id": 3}, "column": "country", "value": "Germany"},
                {"key": {"customer_id": 3}, "column": "revenue", "value": 999},
            ],
        }),
    ]
    _, rel = run(steps)
    idx = rel.column("customer_id").to_pylist().index(3)
    assert rel.column("country").to_pylist()[idx] == "Germany"
    assert rel.column("revenue").to_pylist()[idx] == 999


def test_manual_edit_requires_key():
    with pytest.raises(PipelineError):
        compile_pipeline(
            [src_step(), Step(id="me", type="manual_edit", config={"keys": [], "edits": [{"key": {}, "column": "country", "value": "X"}]})],
            COLS,
        )


def test_pipeline_rejects_unknown_column_and_type():
    with pytest.raises(PipelineError):
        compile_pipeline([src_step(), Step(id="f", type="filter", config={"conditions": [{"column": "nope", "op": "eq", "value": "1"}]})], COLS)
    with pytest.raises(PipelineError):
        compile_pipeline([src_step(), Step(id="x", type="bogus", config={})], COLS)


def test_default_input_is_linear_chain():
    """No input_id set — every step reads the one immediately before it,
    exactly like the pre-DAG behaviour."""
    steps = [
        src_step(),
        Step(id="f1", type="filter", config={"conditions": [{"column": "country", "op": "eq", "value": "NL"}]}),
        Step(id="f2", type="filter", config={"conditions": [{"column": "customer_id", "op": "eq", "value": "1"}]}),
    ]
    _, rel = run(steps)
    assert rel.num_rows == 1
    assert rel.column("customer_id").to_pylist() == [1]


def test_step_can_branch_off_an_earlier_ancestor():
    """Two steps both read directly from the source ('s'), skipping the
    filter in between — a tree, not a line."""
    steps = [
        src_step(),  # id "s"
        Step(id="f_de", type="filter", config={"conditions": [{"column": "country", "op": "eq", "value": "DE"}]}),
        # Branches from source, not from f_de.
        Step(id="f_nl", type="filter", input_id="s", config={"conditions": [{"column": "country", "op": "eq", "value": "NL"}]}),
    ]
    c = compile_pipeline(steps, COLS)
    # Final output is always the last step in list order (f_nl), and since it
    # branched from the source rather than f_de, DE rows never filtered it out.
    con = duckdb.connect()
    con.register("ws", SRC)
    rel = con.execute(f"{c.with_clause} SELECT * FROM {c.final_rel}", c.params).arrow()
    assert rel.column("country").to_pylist() == ["NL"]
    assert rel.num_rows == 1


def test_invalid_input_id_rejected():
    steps = [src_step(), Step(id="f", type="filter", input_id="does-not-exist", config={"conditions": []})]
    with pytest.raises(PipelineError):
        compile_pipeline(steps, COLS)


def test_input_id_cannot_reference_a_later_step():
    # "f2" tries to read from "f3", which comes after it in the list — not
    # yet compiled, so it must be rejected exactly like an unknown id.
    steps = [
        src_step(),
        Step(id="f2", type="filter", input_id="f3", config={"conditions": []}),
        Step(id="f3", type="filter", config={"conditions": []}),
    ]
    with pytest.raises(PipelineError):
        compile_pipeline(steps, COLS)
