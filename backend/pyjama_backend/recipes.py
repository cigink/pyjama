"""Intent recipes (Phase 12, P12.9 — DuckDB-Native Analytical Engine §9).

Reusable, deterministic, versioned mappings from human analytical intent
("show me the trend", "top 5 per state", "compare to last month") to the
canonical operators already built in analysis_spec.py. Recipes are not a new
engine primitive — every recipe function here returns a plain AnalysisSpec,
inspectable and editable exactly like a hand-built one (§9.1's contract).

Reconcile/Unmatched/Cohort recipes need the Explore-side Join operator
(Epic BG, not yet built) and are deliberately not included here yet.
Outliers needs externally-supplied bounds (min/max/mean/stddev) since a
single AnalysisSpec is one query — computing thresholds and filtering by
them in one deterministic pass isn't representable without a second query
round-trip, which the caller (not this module) owns.
"""

from __future__ import annotations

from .analysis_spec import AnalysisSpec, DeriveColumn, FilterCond, Measure, SortSpec, WindowExpr, WindowFrame
from .expression import Binary, Cast, ColumnRef, FunctionCall, Literal

GRAINS = {"day", "week", "month", "quarter", "year"}


class RecipeError(Exception):
    pass


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise RecipeError(message)


def summarize(dimensions: list[str], measures: list[Measure], sort_desc: bool = True, limit: int = 500) -> AnalysisSpec:
    """Summarize — Aggregate -> Order(optional) (§9 table)."""
    _require(bool(measures), "summarize requires at least one measure")
    sort = [SortSpec(column=measures[0].output_name(), direction="desc" if sort_desc else "asc")]
    return AnalysisSpec(dimensions=list(dimensions), measures=list(measures), sort=sort, limit=limit)


def trend(date_column: str, grain: str, measure: Measure, group: str | None = None, limit: int = 500) -> AnalysisSpec:
    """Trend — Derive date bucket -> Aggregate -> Order (§9 table)."""
    _require(grain in GRAINS, f"unknown grain: {grain}")
    bucket = f"{date_column}_{grain}"
    derive = [DeriveColumn(name=bucket, expr=FunctionCall("date_trunc", [Cast(Literal(grain), "VARCHAR"), ColumnRef(date_column)]), result_type="DATE")]
    dims = ([group] if group else []) + [bucket]
    return AnalysisSpec(derive=derive, dimensions=dims, measures=[measure], sort=[SortSpec(column=bucket, direction="asc")], limit=limit)


def top_bottom_n(dimension: str, measure: Measure, n: int, partition: str | None = None, mode: str = "top", limit: int = 500) -> AnalysisSpec:
    """Top / Bottom N — Aggregate -> Window(rank) -> Qualify (§9 table)."""
    _require(n > 0, "n must be positive")
    _require(mode in ("top", "bottom"), "mode must be 'top' or 'bottom'")
    dims = ([partition] if partition else []) + [dimension]
    direction = "desc" if mode == "top" else "asc"
    w = WindowExpr(function_id="rank", partition_by=[partition] if partition else [], order_by=[SortSpec(column=measure.output_name(), direction=direction)], alias="rank")
    sort = ([SortSpec(column=partition)] if partition else []) + [SortSpec(column="rank")]
    return AnalysisSpec(dimensions=dims, measures=[measure], window=[w], qualify=[FilterCond(column="rank", operator="<=", value=n)], sort=sort, limit=limit)


def compare_periods(date_column: str, grain: str, measure: Measure, group: str | None = None, limit: int = 500) -> AnalysisSpec:
    """Compare periods — Aggregate -> Window(lag) -> Derive delta/% (§9
    table; the doc's month-over-month worked example, §29.2)."""
    _require(grain in GRAINS, f"unknown grain: {grain}")
    bucket = f"{date_column}_{grain}"
    derive = [DeriveColumn(name=bucket, expr=FunctionCall("date_trunc", [Cast(Literal(grain), "VARCHAR"), ColumnRef(date_column)]), result_type="DATE")]
    dims = ([group] if group else []) + [bucket]
    measure_name = measure.output_name()
    prev_name = f"prev_{measure_name}"
    delta_name = f"{measure_name}_delta"
    pct_name = f"{measure_name}_pct_change"
    w = WindowExpr(
        function_id="lag", args=[ColumnRef(measure_name)],
        partition_by=[group] if group else [],
        order_by=[SortSpec(column=bucket, direction="asc")],
        alias=prev_name,
    )
    window_derive = [
        DeriveColumn(name=delta_name, expr=Binary("subtract", ColumnRef(measure_name), ColumnRef(prev_name)), result_type="DOUBLE"),
        DeriveColumn(
            name=pct_name,
            expr=Binary("divide", Binary("subtract", ColumnRef(measure_name), ColumnRef(prev_name)), FunctionCall("nullif", [ColumnRef(prev_name), Literal(0.0)])),
            result_type="DOUBLE",
        ),
    ]
    return AnalysisSpec(derive=derive, dimensions=dims, measures=[measure], window=[w], window_derive=window_derive, sort=[SortSpec(column=bucket, direction="asc")], limit=limit)


def running_total(order_column: str, measure: Measure, partition: str | None = None, limit: int = 500) -> AnalysisSpec:
    """Running total — Aggregate(optional) -> Window(sum, unbounded-preceding frame)."""
    measure_name = measure.output_name()
    w = WindowExpr(
        function_id="sum", args=[ColumnRef(measure_name)],
        partition_by=[partition] if partition else [],
        order_by=[SortSpec(column=order_column, direction="asc")],
        frame=WindowFrame(unit="rows", preceding=None, following=0),
        alias=f"running_{measure_name}",
    )
    dims = ([partition] if partition else []) + [order_column]
    return AnalysisSpec(dimensions=dims, measures=[measure], window=[w], sort=[SortSpec(column=order_column, direction="asc")], limit=limit)


def moving_average(order_column: str, measure: Measure, window_width: int, partition: str | None = None, limit: int = 500) -> AnalysisSpec:
    """Moving average — Aggregate(optional) -> Window(avg, N-preceding frame)."""
    _require(window_width > 0, "window_width must be positive")
    measure_name = measure.output_name()
    w = WindowExpr(
        function_id="avg", args=[ColumnRef(measure_name)],
        partition_by=[partition] if partition else [],
        order_by=[SortSpec(column=order_column, direction="asc")],
        frame=WindowFrame(unit="rows", preceding=window_width - 1, following=0),
        alias=f"moving_avg_{measure_name}",
    )
    dims = ([partition] if partition else []) + [order_column]
    return AnalysisSpec(dimensions=dims, measures=[measure], window=[w], sort=[SortSpec(column=order_column, direction="asc")], limit=limit)


def contribution(dimension: str, measure: Measure, partition: str | None = None, limit: int = 500) -> AnalysisSpec:
    """Contribution / share — Aggregate -> Window(total) -> Derive ratio."""
    measure_name = measure.output_name()
    total_name = f"total_{measure_name}"
    share_name = f"{measure_name}_share"
    w = WindowExpr(function_id="sum", args=[ColumnRef(measure_name)], partition_by=[partition] if partition else [], alias=total_name)
    window_derive = [DeriveColumn(name=share_name, expr=Binary("divide", ColumnRef(measure_name), FunctionCall("nullif", [ColumnRef(total_name), Literal(0.0)])), result_type="DOUBLE")]
    dims = ([partition] if partition else []) + [dimension]
    return AnalysisSpec(dimensions=dims, measures=[measure], window=[w], window_derive=window_derive, sort=[SortSpec(column=share_name, direction="desc")], limit=limit)


def duplicates(keys: list[str], tie_breaker: SortSpec | None = None, limit: int = 500) -> AnalysisSpec:
    """Duplicates — Window(row_number/count) -> Qualify. Requires a
    deterministic tie-breaker; the doc explicitly warns results aren't
    reproducible enough for workflow promotion without one (§29.4)."""
    _require(bool(keys), "duplicates requires at least one key column")
    order_by = [tie_breaker] if tie_breaker else []
    w = WindowExpr(function_id="row_number", partition_by=list(keys), order_by=order_by, alias="dup_rank")
    return AnalysisSpec(window=[w], qualify=[FilterCond(column="dup_rank", operator=">", value=1)], sort=[SortSpec(column=k) for k in keys], limit=limit)


def missing_values(field: str, limit: int = 500) -> AnalysisSpec:
    """Missing values — Filter(IS NULL) (§9 table)."""
    return AnalysisSpec(filters=[FilterCond(column=field, operator="is_null")], limit=limit)


def distribution(field: str, bucket_width: float, limit: int = 500) -> AnalysisSpec:
    """Distribution — deterministic fixed-width bucket histogram (numeric
    field). The app, not DuckDB's sampling, owns the bin strategy (§16.3)."""
    _require(bucket_width > 0, "bucket_width must be positive")
    bucket_col = f"{field}_bucket"
    derive = [DeriveColumn(
        name=bucket_col,
        expr=Binary("multiply", FunctionCall("floor", [Binary("divide", ColumnRef(field), Literal(bucket_width))]), Literal(bucket_width)),
        result_type="DOUBLE",
    )]
    return AnalysisSpec(derive=derive, dimensions=[bucket_col], measures=[Measure(column="*", aggregation="count", alias="n")], sort=[SortSpec(column=bucket_col, direction="asc")], limit=limit)
