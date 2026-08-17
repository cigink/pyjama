import duckdb
import pyarrow as pa
import pytest

from pyjama_backend.expression import (
    Between,
    Binary,
    CaseWhen,
    Cast,
    ColumnRef,
    ExpressionError,
    FunctionCall,
    InList,
    IsNull,
    Like,
    Literal,
    Unary,
    compile_expression,
    expr_from_dict,
)

COLS = ["revenue", "cost", "country", "notes"]


def run(expr):
    sql, params = compile_expression(expr, COLS)
    con = duckdb.connect()
    con.register("t", pa.table({"revenue": [100.0], "cost": [40.0], "country": ["NL"], "notes": [None]}))
    return con.execute(f"SELECT {sql} AS v FROM t", params).fetchone()[0]


def test_column_ref_and_binary_arithmetic():
    expr = Binary("subtract", ColumnRef("revenue"), ColumnRef("cost"))
    assert run(expr) == 60.0


def test_literal_is_parameterized():
    sql, params = compile_expression(Binary("gt", ColumnRef("revenue"), Literal(50.0)), COLS)
    assert "?" in sql
    assert params == [50.0]


def test_unknown_column_rejected():
    with pytest.raises(ExpressionError):
        compile_expression(ColumnRef("nope"), COLS)


def test_function_call():
    expr = FunctionCall("round", [ColumnRef("revenue"), Literal(0)])
    assert run(expr) == 100.0


def test_unknown_function_rejected():
    with pytest.raises(ExpressionError):
        compile_expression(FunctionCall("read_csv", [Literal("x")]), COLS)


def test_wrong_arg_count_rejected():
    with pytest.raises(ExpressionError):
        compile_expression(FunctionCall("upper", []), COLS)


def test_aggregate_blocked_in_row_level_context():
    with pytest.raises(ExpressionError):
        compile_expression(FunctionCall("sum", [ColumnRef("revenue")]), COLS)  # allow_aggregate defaults False


def test_aggregate_allowed_when_context_permits():
    sql, _ = compile_expression(FunctionCall("sum", [ColumnRef("revenue")]), COLS, allow_aggregate=True)
    assert sql == "sum(\"revenue\")"


def test_window_blocked_by_default():
    with pytest.raises(ExpressionError):
        compile_expression(FunctionCall("rank", []), COLS)


def test_case_when():
    expr = CaseWhen(branches=[(Binary("gt", ColumnRef("revenue"), Literal(50.0)), Literal("big"))], else_expr=Literal("small"))
    assert run(expr) == "big"


def test_cast():
    expr = Cast(ColumnRef("revenue"), "VARCHAR")
    assert run(expr) == "100.0"


def test_in_list():
    expr = InList(ColumnRef("country"), [Literal("NL"), Literal("DE")])
    assert run(expr) is True


def test_between():
    expr = Between(ColumnRef("revenue"), Literal(0.0), Literal(200.0))
    assert run(expr) is True


def test_is_null():
    assert run(IsNull(ColumnRef("notes"))) is True
    assert run(IsNull(ColumnRef("revenue"))) is False


def test_like():
    expr = Like(ColumnRef("country"), Literal("N%"))
    assert run(expr) is True


def test_unary_negate():
    assert run(Unary("neg", ColumnRef("revenue"))) == -100.0


def test_unknown_binary_op_rejected():
    with pytest.raises(ExpressionError):
        compile_expression(Binary("xor", ColumnRef("revenue"), Literal(1)), COLS)


def test_expr_from_dict_round_trip():
    d = {
        "type": "binary", "op": "subtract",
        "left": {"type": "column_ref", "column": "revenue"},
        "right": {"type": "column_ref", "column": "cost"},
    }
    expr = expr_from_dict(d)
    assert run(expr) == 60.0


def test_expr_from_dict_rejects_malformed():
    with pytest.raises(ExpressionError):
        expr_from_dict({"not_a_type": 1})
    with pytest.raises(ExpressionError):
        expr_from_dict({"type": "bogus"})


def test_no_sql_injection_via_literal():
    sql, params = compile_expression(Binary("eq", ColumnRef("country"), Literal("'; DROP TABLE t; --")), COLS)
    assert "DROP TABLE" not in sql
    assert params == ["'; DROP TABLE t; --"]
