from datetime import date

from sqlchat.charts import suggest_chart
from sqlchat.db import Column, QueryResult


def result(columns: list[tuple[str, str]], rows: list[tuple]) -> QueryResult:
    return QueryResult([Column(n, k) for n, k in columns], rows)  # type: ignore[arg-type]


def test_label_and_number_is_a_bar_chart(revenue_result: QueryResult) -> None:
    chart = suggest_chart(revenue_result)
    assert chart is not None
    assert (chart.kind, chart.x, chart.y) == ("bar", "city", "revenue")


def test_time_axis_is_a_line_chart() -> None:
    chart = suggest_chart(
        result(
            [("month", "time"), ("orders", "number")],
            [(date(2026, 1, 1), 10), (date(2026, 2, 1), 12), (date(2026, 3, 1), 9)],
        )
    )
    assert chart is not None
    assert (chart.kind, chart.x, chart.y) == ("line", "month", "orders")


def test_id_columns_are_not_plotted() -> None:
    chart = suggest_chart(
        result(
            [("store_id", "number"), ("name", "text"), ("revenue", "number")],
            [(1, "Kemang", 100), (2, "Braga", 80)],
        )
    )
    assert chart is not None
    assert chart.y == "revenue"


def test_two_numbers_use_the_first_as_axis() -> None:
    chart = suggest_chart(result([("hour", "number"), ("orders", "number")], [(8, 40), (9, 31)]))
    assert chart is not None
    assert (chart.x, chart.y) == ("hour", "orders")


def test_single_row_has_no_chart() -> None:
    assert (
        suggest_chart(result([("city", "text"), ("revenue", "number")], [("Jakarta", 1)])) is None
    )


def test_long_results_have_no_chart() -> None:
    rows = [(f"customer {i}", i) for i in range(200)]
    assert suggest_chart(result([("name", "text"), ("orders", "number")], rows)) is None


def test_text_only_results_have_no_chart() -> None:
    rows = [("Kemang", "Jakarta"), ("Braga", "Bandung")]
    assert suggest_chart(result([("name", "text"), ("city", "text")], rows)) is None


def test_two_label_columns_are_ambiguous() -> None:
    rows = [("Kemang", "Jakarta", 5), ("Braga", "Bandung", 4)]
    columns = [("name", "text"), ("city", "text"), ("orders", "number")]
    assert suggest_chart(result(columns, rows)) is None
