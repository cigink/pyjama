import datetime

import duckdb
import pyarrow as pa
import pytest

from pyjama_backend import recipes
from pyjama_backend.analysis_spec import Measure, SortSpec, compile_analysis

COLS = ["region", "month", "revenue", "order_id", "customer_id"]
TYPES = {"region": "VARCHAR", "month": "DATE", "revenue": "DOUBLE", "order_id": "VARCHAR", "customer_id": "BIGINT"}

TABLE = pa.table({
    "region": ["East", "East", "East", "West", "West", "West"],
    "month": pa.array(
        [datetime.date(2026, 1, 1), datetime.date(2026, 2, 1), datetime.date(2026, 3, 1), datetime.date(2026, 1, 1), datetime.date(2026, 2, 1), datetime.date(2026, 3, 1)],
        type=pa.date32(),
    ),
    "revenue": [100.0, 150.0, 120.0, 200.0, 180.0, 220.0],
    "order_id": ["o1", "o2", "o3", "o4", "o5", "o6"],
    "customer_id": [1, 1, 2, 3, 3, 3],
})


def run(spec):
    con = duckdb.connect()
    con.register("current", TABLE)
    c = compile_analysis(spec, "current", COLS, TYPES)
    return c, con.execute(c.sql, c.params).arrow()


def test_summarize():
    spec = recipes.summarize(dimensions=["region"], measures=[Measure(column="revenue", aggregation="sum")])
    c, rel = run(spec)
    d = dict(zip(rel.column("region").to_pylist(), rel.column("sum_revenue").to_pylist()))
    assert d["East"] == 370.0 and d["West"] == 600.0
    # sorted descending by the measure
    assert rel.column("region").to_pylist()[0] == "West"


def test_summarize_requires_measure():
    with pytest.raises(recipes.RecipeError):
        recipes.summarize(dimensions=["region"], measures=[])


def test_trend():
    spec = recipes.trend(date_column="month", grain="month", measure=Measure(column="revenue", aggregation="sum"))
    c, rel = run(spec)
    assert "month_month" in c.output_columns
    assert rel.num_rows == 3  # 3 distinct months, no region grouping
    months = rel.column("month_month").to_pylist()
    assert months == sorted(months)  # ascending order


def test_trend_unknown_grain():
    with pytest.raises(recipes.RecipeError):
        recipes.trend(date_column="month", grain="fortnight", measure=Measure(column="revenue", aggregation="sum"))


def test_top_n_per_partition():
    spec = recipes.top_bottom_n(dimension="month", measure=Measure(column="revenue", aggregation="sum"), n=1, partition="region", mode="top")
    c, rel = run(spec)
    assert "QUALIFY" in c.sql
    regions = rel.column("region").to_pylist()
    assert len(regions) == len(set(regions))  # exactly one row per region
    d = dict(zip(regions, rel.column("month").to_pylist()))
    assert d["East"] == datetime.date(2026, 2, 1)  # East's highest-revenue month
    assert d["West"] == datetime.date(2026, 3, 1)


def test_bottom_n():
    spec = recipes.top_bottom_n(dimension="month", measure=Measure(column="revenue", aggregation="sum"), n=1, partition="region", mode="bottom")
    c, rel = run(spec)
    d = dict(zip(rel.column("region").to_pylist(), rel.column("month").to_pylist()))
    assert d["East"] == datetime.date(2026, 1, 1)  # East's lowest-revenue month


def test_compare_periods_delta_and_pct():
    spec = recipes.compare_periods(date_column="month", grain="month", measure=Measure(column="revenue", aggregation="sum"), group="region")
    c, rel = run(spec)
    assert "sum_revenue_delta" in c.output_columns
    assert "sum_revenue_pct_change" in c.output_columns
    # Feb delta vs Jan = 150 - 100 = 50
    feb = [r for r in rel.to_pylist() if r["region"] == "East" and r["month_month"].month == 2][0]
    assert feb["sum_revenue_delta"] == 50.0
    assert feb["sum_revenue_pct_change"] == pytest.approx(0.5)


def test_running_total():
    spec = recipes.running_total(order_column="month", measure=Measure(column="revenue", aggregation="sum"), partition="region")
    c, rel = run(spec)
    assert "UNBOUNDED PRECEDING" in c.sql
    east = sorted([r for r in rel.to_pylist() if r["region"] == "East"], key=lambda r: r["month"])
    running = [r["running_sum_revenue"] for r in east]
    assert running == [100.0, 250.0, 370.0]


def test_moving_average():
    spec = recipes.moving_average(order_column="month", measure=Measure(column="revenue", aggregation="sum"), window_width=2, partition="region")
    c, rel = run(spec)
    assert "1 PRECEDING" in c.sql  # window_width=2 -> 1 PRECEDING + CURRENT ROW = 2 rows
    east = sorted([r for r in rel.to_pylist() if r["region"] == "East"], key=lambda r: r["month"])
    assert east[0]["moving_avg_sum_revenue"] == 100.0  # only itself in the window
    assert east[1]["moving_avg_sum_revenue"] == 125.0  # avg(100, 150)


def test_moving_average_rejects_non_positive_width():
    with pytest.raises(recipes.RecipeError):
        recipes.moving_average(order_column="month", measure=Measure(column="revenue", aggregation="sum"), window_width=0)


def test_contribution_share_sums_to_one_per_partition():
    spec = recipes.contribution(dimension="month", measure=Measure(column="revenue", aggregation="sum"), partition="region")
    c, rel = run(spec)
    east_share = sum(r["sum_revenue_share"] for r in rel.to_pylist() if r["region"] == "East")
    assert east_share == pytest.approx(1.0)


def test_duplicates_requires_keys():
    with pytest.raises(recipes.RecipeError):
        recipes.duplicates(keys=[])


def test_duplicates_finds_repeat_customers():
    spec = recipes.duplicates(keys=["customer_id"], tie_breaker=SortSpec(column="order_id", direction="asc"))
    c, rel = run(spec)
    assert "QUALIFY" in c.sql
    # customer 1 (2 orders) and customer 3 (3 orders) each contribute rows beyond the first
    assert rel.num_rows == (2 - 1) + (3 - 1)


def test_missing_values():
    spec = recipes.missing_values(field="region")
    c, rel = run(spec)
    assert rel.num_rows == 0  # fixture has no nulls in region
    assert "IS NULL" in c.sql


def test_distribution_buckets():
    spec = recipes.distribution(field="revenue", bucket_width=50.0)
    c, rel = run(spec)
    assert "region_bucket" not in c.output_columns
    assert "revenue_bucket" in c.output_columns
    total = sum(rel.column("n").to_pylist())
    assert total == 6


def test_distribution_rejects_non_positive_width():
    with pytest.raises(recipes.RecipeError):
        recipes.distribution(field="revenue", bucket_width=0)
