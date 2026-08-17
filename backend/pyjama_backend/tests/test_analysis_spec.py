import io
import shutil

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pyjama_backend import crypto, sources, workspace
from pyjama_backend.analysis_spec import (
    AnalysisError,
    AnalysisSpec,
    DeriveColumn,
    FilterCond,
    JoinSpec,
    Measure,
    SortSpec,
    WindowExpr,
    WindowFrame,
    compile_analysis,
    default_step_name,
    visualization_hint,
)
from pyjama_backend.expression import Binary, ColumnRef, FunctionCall, Literal
from pyjama_backend.keystore import MemoryKeyStore
from pyjama_backend.pipeline import Step
from pyjama_backend.query import WorkspaceSession

COLS = ["patient_id", "gender", "age", "claim_amount", "diagnosis", "visit_date"]
TYPES = {
    "patient_id": "BIGINT", "gender": "VARCHAR", "age": "BIGINT",
    "claim_amount": "DOUBLE", "diagnosis": "VARCHAR", "visit_date": "DATE",
}

TABLE = pa.table({
    "patient_id": [1, 2, 3, 4],
    "gender": ["Female", "Male", "Female", "Female"],
    "age": [62, 47, 71, 30],
    "claim_amount": [4812.0, 1260.0, 9000.0, 500.0],
    "diagnosis": ["J18", "I10", "J18", "I10"],
    "visit_date": ["2026-01-01", "2026-02-01", "2026-01-15", "2026-03-01"],
})


def run(spec: AnalysisSpec):
    con = duckdb.connect()
    con.register("current", TABLE)
    c = compile_analysis(spec, "current", COLS, TYPES)
    rel = con.execute(c.sql, c.params).arrow()
    return c, rel


def test_group_by_and_measure():
    spec = AnalysisSpec(dimensions=["gender"], measures=[Measure(column="claim_amount", aggregation="avg")])
    c, rel = run(spec)
    assert c.output_columns == ["gender", "avg_claim_amount"]
    assert not c.is_raw_rows and not c.is_aggregate
    d = dict(zip(rel.column("gender").to_pylist(), rel.column("avg_claim_amount").to_pylist()))
    assert d["Female"] == pytest.approx((4812.0 + 9000.0 + 500.0) / 3)
    assert d["Male"] == 1260.0


def test_filter_over_age_60():
    spec = AnalysisSpec(
        dimensions=["gender"],
        measures=[Measure(column="claim_amount", aggregation="avg")],
        filters=[FilterCond(column="age", operator=">", value=60)],
    )
    _, rel = run(spec)
    assert set(rel.column("gender").to_pylist()) == {"Female"}  # only ages 62, 71 survive


def test_kpi_single_aggregate():
    spec = AnalysisSpec(measures=[Measure(column="patient_id", aggregation="count")])
    c, rel = run(spec)
    assert c.is_aggregate and not c.is_raw_rows
    assert rel.num_rows == 1
    assert rel.column("count_patient_id").to_pylist()[0] == 4


def test_raw_rows_when_no_dimensions_or_measures():
    spec = AnalysisSpec()
    c, rel = run(spec)
    assert c.is_raw_rows
    assert rel.num_rows == 4
    assert c.output_columns == COLS


def test_count_distinct():
    spec = AnalysisSpec(measures=[Measure(column="diagnosis", aggregation="count_distinct")])
    c, rel = run(spec)
    assert rel.column("count_distinct_diagnosis").to_pylist()[0] == 2


def test_sort_and_limit():
    spec = AnalysisSpec(
        dimensions=["diagnosis"],
        measures=[Measure(column="claim_amount", aggregation="sum")],
        sort=[SortSpec(column="sum_claim_amount", direction="desc")],
        limit=1,
    )
    _, rel = run(spec)
    assert rel.num_rows == 1
    assert rel.column("diagnosis").to_pylist()[0] == "J18"  # 4812+9000 > 1260+500


def test_unknown_dimension_rejected():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(dimensions=["nope"]), "current", COLS, TYPES)


def test_unknown_measure_column_rejected():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(measures=[Measure(column="nope", aggregation="sum")]), "current", COLS, TYPES)


def test_unknown_aggregation_rejected():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(measures=[Measure(column="claim_amount", aggregation="median_absolute_deviation")]), "current", COLS, TYPES)


def test_unknown_filter_operator_rejected():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(filters=[FilterCond(column="age", operator="~=", value=1)]), "current", COLS, TYPES)


def test_sort_must_reference_output_column():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(dimensions=["gender"], sort=[SortSpec(column="claim_amount")]), "current", COLS, TYPES)


def test_limit_is_capped():
    c = compile_analysis(AnalysisSpec(limit=999_999), "current", COLS, TYPES)
    assert "LIMIT 10000" in c.sql


def test_deterministic_compile():
    spec = AnalysisSpec(dimensions=["gender"], measures=[Measure(column="claim_amount", aggregation="avg")], filters=[FilterCond(column="age", operator=">", value=60)])
    c1 = compile_analysis(spec, "current", COLS, TYPES)
    c2 = compile_analysis(spec, "current", COLS, TYPES)
    assert c1.sql == c2.sql
    assert c1.params == c2.params


def test_visualization_hints():
    kpi = compile_analysis(AnalysisSpec(measures=[Measure(column="patient_id", aggregation="count")]), "current", COLS, TYPES)
    assert visualization_hint(AnalysisSpec(measures=[Measure(column="patient_id", aggregation="count")]), kpi, TYPES) == "kpi"

    bar_spec = AnalysisSpec(dimensions=["gender"], measures=[Measure(column="claim_amount", aggregation="avg")])
    bar = compile_analysis(bar_spec, "current", COLS, TYPES)
    assert visualization_hint(bar_spec, bar, TYPES) == "bar"

    line_spec = AnalysisSpec(dimensions=["visit_date"], measures=[Measure(column="claim_amount", aggregation="sum")])
    line = compile_analysis(line_spec, "current", COLS, TYPES)
    assert visualization_hint(line_spec, line, TYPES) == "line"

    table_spec = AnalysisSpec(dimensions=["gender", "diagnosis"], measures=[Measure(column="claim_amount", aggregation="sum")])
    table = compile_analysis(table_spec, "current", COLS, TYPES)
    assert visualization_hint(table_spec, table, TYPES) == "table"

    raw_spec = AnalysisSpec()
    raw = compile_analysis(raw_spec, "current", COLS, TYPES)
    assert visualization_hint(raw_spec, raw, TYPES) == "grid"


def test_filter_value_is_bound_not_interpolated():
    spec = AnalysisSpec(filters=[FilterCond(column="gender", operator="=", value="'; DROP TABLE current; --")])
    c = compile_analysis(spec, "current", COLS, TYPES)
    assert "DROP TABLE" not in c.sql
    assert c.params == ["'; DROP TABLE current; --"]


# ---- live: through WorkspaceSession.run_analysis, on a real encrypted source ----

SRC = pa.table({
    "customer_id": [1, 2, 3, 4],
    "country": ["NL", "NL", "DE", "NL"],
    "revenue": [100.0, 50.0, 90.0, 10.0],
})


def _ws():
    ks = MemoryKeyStore()
    src = sources.create_placeholder("Analysis Test", "uc_table")
    wdek = crypto.load_or_create_wdek(ks, src.source_id)
    buf = io.BytesIO()
    pq.write_table(SRC, buf, compression="zstd")
    (sources.source_data_dir(src.source_id) / "source-00000.parquet").write_bytes(crypto.encrypt(wdek, buf.getvalue()))
    m = workspace.create("Analysis Test", primary_source_id=src.source_id)
    m.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
    workspace.write_manifest(m)
    return ks, m


def _cleanup(m):
    if m.primary_source_id:
        shutil.rmtree(sources.source_dir(m.primary_source_id), ignore_errors=True)
    shutil.rmtree(workspace.workspaces_root() / m.workspace_id, ignore_errors=True)


def test_run_analysis_through_workspace_session():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    spec = AnalysisSpec(dimensions=["country"], measures=[Measure(column="revenue", aggregation="sum")], sort=[SortSpec(column="sum_revenue", direction="desc")])
    result = sess.run_analysis(steps, None, spec)
    assert result["columns"] == ["country", "sum_revenue"]
    assert result["rows"][0] == ["NL", 160.0]  # 100+50+10
    assert result["row_count"] == 2
    assert result["visualization_hint"] == "bar"
    assert "GROUP BY" in result["generated_sql"]
    sess.close(); _cleanup(m)


def test_run_analysis_is_ephemeral():
    """Running an analysis must never touch the saved pipeline/revision."""
    ks, m = _ws()
    before_revision = m.pipeline_revision
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    sess.run_analysis(steps, None, AnalysisSpec(measures=[Measure(column="revenue", aggregation="avg")]))
    reloaded = workspace.read_manifest(m.workspace_id)
    assert reloaded.pipeline_revision == before_revision
    assert reloaded.pipeline == m.pipeline
    sess.close(); _cleanup(m)


# ---- "Keep as workflow" (P11.16) ----

def test_default_step_name():
    assert default_step_name(AnalysisSpec(dimensions=["gender"], measures=[Measure(column="claim_amount", aggregation="avg")])) == "avg claim_amount by gender"
    assert default_step_name(AnalysisSpec(filters=[FilterCond(column="age", operator=">", value=60)])) == "age > 60"
    assert default_step_name(AnalysisSpec()) == "Analysis"


def test_promote_analysis_sql_produces_standalone_current_sql():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    spec = AnalysisSpec(filters=[FilterCond(column="country", operator="=", value="NL")])
    sql = sess.promote_analysis_sql(steps, None, spec)
    assert "current" in sql.lower()
    assert "?" not in sql  # literal-valued, no leftover placeholders
    assert "NL" in sql
    sess.close(); _cleanup(m)


def test_promote_analysis_sql_is_ephemeral_by_itself():
    """Compiling the promotion SQL must not touch the pipeline — only the
    server endpoint's explicit append does that."""
    ks, m = _ws()
    before_revision = m.pipeline_revision
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    sess.promote_analysis_sql(steps, None, AnalysisSpec(measures=[Measure(column="revenue", aggregation="sum")]))
    reloaded = workspace.read_manifest(m.workspace_id)
    assert reloaded.pipeline_revision == before_revision
    sess.close(); _cleanup(m)


def test_promoted_sql_compiles_as_a_real_sql_transform_step():
    """The literal-valued SQL from promote_analysis_sql must survive being
    saved as a sql_transform step and recompiled through the normal pipeline
    (§16.16's actual save path), producing the same result as running the
    analysis directly."""
    from pyjama_backend.pipeline import compile_pipeline

    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    spec = AnalysisSpec(dimensions=["country"], measures=[Measure(column="revenue", aggregation="sum")], sort=[SortSpec(column="sum_revenue", direction="desc")])
    sql = sess.promote_analysis_sql(steps, None, spec)

    promoted_step = Step(id="explore-1", type="sql_transform", config={"sql": sql})
    c = compile_pipeline(steps + [promoted_step], sess.schema_columns(), column_types=sess._duck_types)
    with sess._lock:
        rows = sess._con.execute(f"{c.with_clause} SELECT * FROM {c.final_rel}", c.params).fetchall()
    assert rows[0][0] == "NL" and rows[0][1] == 160.0
    sess.close(); _cleanup(m)


def test_promote_analysis_sql_rejects_unknown_column():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    with pytest.raises(AnalysisError):
        sess.promote_analysis_sql(steps, None, AnalysisSpec(dimensions=["nope"]))
    sess.close(); _cleanup(m)


# ---- Derive (Phase 12, P12.3) ----

def test_derive_column_available_to_filter_dimension_and_sort():
    """A single request: derive a margin-ish column, filter on it, group by
    it as a dimension bucket, and sort by it — all in one AnalysisSpec."""
    spec = AnalysisSpec(
        derive=[DeriveColumn(name="double_claim", expr=Binary("multiply", ColumnRef("claim_amount"), Literal(2.0)), result_type="DOUBLE")],
        dimensions=["gender"],
        measures=[Measure(column="double_claim", aggregation="sum")],
        filters=[FilterCond(column="double_claim", operator=">", value=0)],
        sort=[SortSpec(column="sum_double_claim", direction="desc")],
    )
    c, rel = run(spec)
    assert c.output_columns == ["gender", "sum_double_claim"]
    d = dict(zip(rel.column("gender").to_pylist(), rel.column("sum_double_claim").to_pylist()))
    assert d["Female"] == pytest.approx((4812.0 + 9000.0 + 500.0) * 2)


def test_derive_with_function_call():
    spec = AnalysisSpec(derive=[DeriveColumn(name="claim_bucket", expr=FunctionCall("round", [ColumnRef("claim_amount"), Literal(-3)]), result_type="DOUBLE")])
    c, rel = run(spec)
    assert "claim_bucket" in c.output_columns
    assert rel.num_rows == 4


def test_derive_rejects_unknown_column():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(derive=[DeriveColumn(name="x", expr=ColumnRef("nope"))]), "current", COLS, TYPES)


def test_derive_rejects_aggregate_expression():
    """§8.6: aggregate expressions must never sneak into row-level Derive."""
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(derive=[DeriveColumn(name="x", expr=FunctionCall("sum", [ColumnRef("claim_amount")]))]), "current", COLS, TYPES)


def test_derive_rejects_name_collision():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(derive=[DeriveColumn(name="gender", expr=Literal("x"))]), "current", COLS, TYPES)


def test_derive_promotion_produces_inline_standalone_sql():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    spec = AnalysisSpec(derive=[DeriveColumn(name="double_revenue", expr=Binary("multiply", ColumnRef("revenue"), Literal(2.0)), result_type="DOUBLE")])
    sql = sess.promote_analysis_sql(steps, None, spec)
    assert "?" not in sql
    assert "double_revenue" in sql
    sess.close(); _cleanup(m)


# ---- Distinct, Having, Window, Qualify (Phase 12, P12.4-P12.8) ----

def test_distinct():
    spec = AnalysisSpec(distinct=True)
    c, rel = run(spec)
    assert "DISTINCT" in c.sql
    assert rel.num_rows == 4  # no duplicate full rows in the fixture


def test_having_filters_grouped_rows():
    spec = AnalysisSpec(
        dimensions=["diagnosis"],
        measures=[Measure(column="patient_id", aggregation="count")],
        having=[FilterCond(column="count_patient_id", operator=">", value=1)],
    )
    c, rel = run(spec)
    assert "HAVING" in c.sql
    # both diagnoses (J18, I10) appear twice in the fixture, so both survive having count > 1
    assert set(rel.column("diagnosis").to_pylist()) == {"J18", "I10"}
    assert all(v > 1 for v in rel.column("count_patient_id").to_pylist())


def test_having_requires_aggregate():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(having=[FilterCond(column="age", operator=">", value=0)]), "current", COLS, TYPES)


def test_having_rejects_unknown_output_column():
    with pytest.raises(AnalysisError):
        compile_analysis(
            AnalysisSpec(dimensions=["gender"], measures=[Measure(column="claim_amount", aggregation="sum")], having=[FilterCond(column="nope", operator=">", value=0)]),
            "current", COLS, TYPES,
        )


def test_window_rank_row_level():
    spec = AnalysisSpec(window=[WindowExpr(function_id="rank", partition_by=["gender"], order_by=[SortSpec(column="claim_amount", direction="desc")], alias="rnk")])
    c, rel = run(spec)
    assert "OVER" in c.sql
    assert "rnk" in c.output_columns
    assert rel.num_rows == 4  # window never removes rows


def test_window_rejects_disallowed_function():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(window=[WindowExpr(function_id="upper", alias="x")]), "current", COLS, TYPES)


def test_window_aggregate_as_running_total():
    spec = AnalysisSpec(
        window=[WindowExpr(
            function_id="sum", args=[ColumnRef("claim_amount")],
            order_by=[SortSpec(column="visit_date", direction="asc")],
            frame=WindowFrame(unit="rows", preceding=None, following=0),
            alias="running_total",
        )]
    )
    c, rel = run(spec)
    assert "UNBOUNDED PRECEDING" in c.sql
    assert "running_total" in c.output_columns


def test_qualify_top_n_per_partition():
    """The doc's worked example (§29.1): top diagnoses per state via
    Aggregate -> Window(rank) -> Qualify -> Order, in one multi-stage compile."""
    spec = AnalysisSpec(
        dimensions=["gender", "diagnosis"],
        measures=[Measure(column="claim_amount", aggregation="avg")],
        window=[WindowExpr(function_id="rank", partition_by=["gender"], order_by=[SortSpec(column="avg_claim_amount", direction="desc")], alias="diagnosis_rank")],
        qualify=[FilterCond(column="diagnosis_rank", operator="<=", value=1)],
        sort=[SortSpec(column="gender"), SortSpec(column="diagnosis_rank")],
    )
    c, rel = run(spec)
    assert "QUALIFY" in c.sql
    assert "WITH" in c.sql  # multi-stage: aggregate CTE -> window CTE -> qualify
    # exactly one row per gender survives rank <= 1
    genders = rel.column("gender").to_pylist()
    assert len(genders) == len(set(genders))


def test_qualify_requires_window():
    with pytest.raises(AnalysisError):
        compile_analysis(AnalysisSpec(qualify=[FilterCond(column="x", operator=">", value=0)]), "current", COLS, TYPES)


def test_multistage_pipeline_is_deterministic():
    spec = AnalysisSpec(
        filters=[FilterCond(column="age", operator=">", value=0)],
        dimensions=["gender"],
        measures=[Measure(column="claim_amount", aggregation="avg")],
        window=[WindowExpr(function_id="rank", order_by=[SortSpec(column="avg_claim_amount", direction="desc")], alias="r")],
        qualify=[FilterCond(column="r", operator="<=", value=5)],
    )
    c1 = compile_analysis(spec, "current", COLS, TYPES)
    c2 = compile_analysis(spec, "current", COLS, TYPES)
    assert c1.sql == c2.sql
    assert c1.params == c2.params


def test_window_and_qualify_promote_to_standalone_sql():
    ks, m = _ws()
    sess = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    spec = AnalysisSpec(
        window=[WindowExpr(function_id="row_number", order_by=[SortSpec(column="revenue", direction="desc")], alias="rn")],
        qualify=[FilterCond(column="rn", operator="<=", value=1)],
    )
    sql = sess.promote_analysis_sql(steps, None, spec)
    assert "?" not in sql
    assert "QUALIFY" in sql
    sess.close(); _cleanup(m)


# ---- Join (Phase 12, P12.10) ----

RIGHT_TABLE = pa.table({"country": ["NL", "DE"], "country_name": ["Netherlands", "Germany"], "region": ["EU", "EU"]})
LOCAL_SOURCES = {"src-1": {"rel": "right_rel", "columns": ["country", "country_name", "region"]}}


def run_with_join(spec):
    con = duckdb.connect()
    con.register("current", TABLE)
    con.register("right_rel", RIGHT_TABLE)
    c = compile_analysis(spec, "current", COLS, TYPES, local_sources=LOCAL_SOURCES)
    return c, con.execute(c.sql, c.params).arrow()


def test_left_join_adds_right_columns():
    spec = AnalysisSpec(join=JoinSpec(local_source_id="src-1", join_type="left", keys=[("diagnosis", "country")]))
    c, rel = run_with_join(spec)
    assert "LEFT JOIN" in c.sql
    assert "country_name" in c.output_columns and "region" in c.output_columns
    assert rel.num_rows == 4  # left join never drops left rows


def test_inner_join_filters_to_matches():
    spec = AnalysisSpec(join=JoinSpec(local_source_id="src-1", join_type="inner", keys=[("diagnosis", "country")]))
    c, rel = run_with_join(spec)
    assert "INNER JOIN" in c.sql
    assert rel.num_rows == 0  # no diagnosis code matches a country code in this fixture


def test_semi_join_keeps_only_left_columns():
    spec = AnalysisSpec(join=JoinSpec(local_source_id="src-1", join_type="semi", keys=[("diagnosis", "country")]))
    c, rel = run_with_join(spec)
    assert "SEMI JOIN" in c.sql
    assert "country_name" not in c.output_columns
    assert set(c.output_columns) == set(COLS)


def test_anti_join_keeps_unmatched_rows():
    spec = AnalysisSpec(join=JoinSpec(local_source_id="src-1", join_type="anti", keys=[("diagnosis", "country")]))
    c, rel = run_with_join(spec)
    assert "ANTI JOIN" in c.sql
    assert rel.num_rows == 4  # nothing in `diagnosis` matches `country`, so all rows survive


def test_join_column_collision_gets_suffixed():
    # RIGHT_TABLE's "region" doesn't collide, but "country" is a join key
    # (excluded); force a real collision by joining a right table that shares
    # a non-key column name with the left side.
    con = duckdb.connect()
    con.register("current", TABLE)
    right = pa.table({"diagnosis": ["J18", "I10"], "gender": ["collide", "collide"]})  # "gender" collides with left
    con.register("right_rel2", right)
    local_sources = {"src-2": {"rel": "right_rel2", "columns": ["diagnosis", "gender"]}}
    spec = AnalysisSpec(join=JoinSpec(local_source_id="src-2", join_type="left", keys=[("diagnosis", "diagnosis")]))
    c = compile_analysis(spec, "current", COLS, TYPES, local_sources=local_sources)
    assert "gender_right" in c.output_columns
    rel = con.execute(c.sql, c.params).arrow()
    assert "gender_right" in rel.column_names


def test_join_unknown_source_rejected():
    spec = AnalysisSpec(join=JoinSpec(local_source_id="nope", join_type="left", keys=[("diagnosis", "country")]))
    with pytest.raises(AnalysisError):
        compile_analysis(spec, "current", COLS, TYPES, local_sources=LOCAL_SOURCES)


def test_join_unknown_type_rejected():
    spec = AnalysisSpec(join=JoinSpec(local_source_id="src-1", join_type="bogus", keys=[("diagnosis", "country")]))
    with pytest.raises(AnalysisError):
        compile_analysis(spec, "current", COLS, TYPES, local_sources=LOCAL_SOURCES)


def test_join_requires_keys():
    spec = AnalysisSpec(join=JoinSpec(local_source_id="src-1", join_type="left", keys=[]))
    with pytest.raises(AnalysisError):
        compile_analysis(spec, "current", COLS, TYPES, local_sources=LOCAL_SOURCES)


def test_join_unknown_right_key_rejected():
    spec = AnalysisSpec(join=JoinSpec(local_source_id="src-1", join_type="left", keys=[("diagnosis", "nope")]))
    with pytest.raises(AnalysisError):
        compile_analysis(spec, "current", COLS, TYPES, local_sources=LOCAL_SOURCES)


def test_join_then_aggregate_survives_promotion_and_reopen():
    """The real regression this story fixed: a promoted join step must stay
    resolvable when the WorkspaceSession is closed and reopened (a fresh
    instance, exactly like closing and reopening the app), not just in the
    same session it was promoted in."""
    from pyjama_backend import localsource

    ks, m = _ws()
    right_src = localsource.import_bytes(ks, "countries.csv", "csv", b"country,country_name\nNL,Netherlands\nDE,Germany\n")
    m2 = workspace.read_manifest(m.workspace_id)
    m2.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
    workspace.write_manifest(m2)

    sess1 = WorkspaceSession(m.workspace_id, ks)
    steps = [Step(id="src", type="source")]
    spec = AnalysisSpec(join=JoinSpec(local_source_id=right_src.source_id, join_type="left", keys=[("country", "country")]))
    sql = sess1.promote_analysis_sql(steps, None, spec)
    assert "LEFT JOIN" in sql
    sess1.close()

    m3 = workspace.read_manifest(m.workspace_id)
    m3.pipeline.append({"id": "explore-join-1", "type": "sql_transform", "config": {"sql": sql, "local_source_id": right_src.source_id}, "enabled": True})
    workspace.write_manifest(m3)

    # Fresh session — simulates closing and reopening the workspace.
    sess2 = WorkspaceSession(m.workspace_id, ks)
    assert right_src.source_id in sess2._local_sources
    table, _schema = sess2.step_output([Step(id="src", type="source"), Step(id="explore-join-1", type="sql_transform", config={"sql": sql, "local_source_id": right_src.source_id})], None)
    assert "country_name" in table.column_names
    sess2.close()

    shutil.rmtree(sources.source_dir(right_src.source_id), ignore_errors=True)
    _cleanup(m)
