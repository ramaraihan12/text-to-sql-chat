"""Decide whether a result is worth a chart, and which kind. No model call involved."""

from dataclasses import dataclass
from typing import Literal

from sqlchat.db import Column, QueryResult

MAX_POINTS = 60


@dataclass(frozen=True)
class ChartSpec:
    kind: Literal["bar", "line"]
    x: str
    y: str


def suggest_chart(result: QueryResult) -> ChartSpec | None:
    """A bar or line chart for "label + number" shaped results, otherwise None.

    One row is a single fact and reads better as text; long results are a table, not a chart.
    """
    if not 2 <= len(result.rows) <= MAX_POINTS:
        return None

    labels = [c for c in result.columns if c.kind != "number"]
    measures = [c for c in result.columns if c.kind == "number" and not _looks_like_id(c)]

    if len(labels) == 1 and measures:
        x = labels[0]
        return ChartSpec("line" if x.kind == "time" else "bar", x.name, measures[-1].name)

    # "orders per hour" style results: two numbers, the first one is the axis.
    if not labels and len(result.columns) == 2:
        x, y = result.columns
        return ChartSpec("bar", x.name, y.name)

    return None


def _looks_like_id(column: Column) -> bool:
    name = column.name.lower()
    return name == "id" or name.endswith("_id")
