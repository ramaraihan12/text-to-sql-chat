from datetime import date
from decimal import Decimal

import psycopg
import pytest
from mcp import Client

from sqlchat.db import Column, QueryResult
from sqlchat.mcp_server import build_server
from tests.fakes import FakeDatabase, make_tools


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def monthly() -> QueryResult:
    return QueryResult(
        columns=[Column("month", "time"), Column("revenue", "number")],
        rows=[(date(2026, 8, 1), Decimal("4210000")), (date(2026, 9, 1), Decimal("4446000.50"))],
    )


@pytest.mark.anyio
async def test_lists_three_read_only_tools() -> None:
    async with Client(build_server(make_tools(FakeDatabase()))) as client:
        tools = (await client.list_tools()).tools

    assert sorted(t.name for t in tools) == ["check_sql", "describe_schema", "run_sql"]
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


@pytest.mark.anyio
async def test_describe_schema_returns_the_prompt_schema() -> None:
    async with Client(build_server(make_tools(FakeDatabase()))) as client:
        result = await client.call_tool("describe_schema", {})

    assert not result.is_error
    assert "TABLE stores" in result.content[0].text


@pytest.mark.anyio
async def test_run_sql_returns_json_friendly_rows() -> None:
    db = FakeDatabase(monthly())
    async with Client(build_server(make_tools(db, max_rows=50))) as client:
        result = await client.call_tool(
            "run_sql", {"sql": "SELECT date_trunc('month', ordered_at) AS month FROM orders"}
        )

    assert not result.is_error
    data = result.structured_content
    assert data is not None
    assert data["columns"] == ["month", "revenue"]
    assert data["rows"] == [["2026-08-01", 4210000], ["2026-09-01", 4446000.5]]
    assert data["row_count"] == 2
    assert data["limit_reached"] is False
    assert data["sql"].endswith("LIMIT 50")
    assert db.executed == [data["sql"]]


@pytest.mark.anyio
async def test_run_sql_rejects_writes_without_touching_the_database() -> None:
    db = FakeDatabase()
    async with Client(build_server(make_tools(db))) as client:
        result = await client.call_tool("run_sql", {"sql": "DROP TABLE orders"})

    assert result.is_error
    assert "Only SELECT" in result.content[0].text
    assert db.executed == []


@pytest.mark.anyio
async def test_run_sql_reports_postgres_errors_to_the_model() -> None:
    db = FakeDatabase(psycopg.errors.UndefinedColumn('column "total" does not exist'))
    async with Client(build_server(make_tools(db))) as client:
        result = await client.call_tool("run_sql", {"sql": "SELECT total FROM orders"})

    assert result.is_error
    assert 'column "total" does not exist' in result.content[0].text


@pytest.mark.anyio
async def test_check_sql_explains_rejections() -> None:
    async with Client(build_server(make_tools(FakeDatabase()))) as client:
        ok = await client.call_tool("check_sql", {"sql": "SELECT * FROM stores"})
        bad = await client.call_tool("check_sql", {"sql": "SELECT pg_sleep(30)"})

    assert ok.structured_content == {
        "allowed": True,
        "sql": "SELECT\n  *\nFROM stores\nLIMIT 100",
        "reason": None,
    }
    assert bad.structured_content is not None
    assert bad.structured_content["allowed"] is False
    assert "pg_sleep" in bad.structured_content["reason"]
