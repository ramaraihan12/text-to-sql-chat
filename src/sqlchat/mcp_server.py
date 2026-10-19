"""MCP server over stdio, so Claude Desktop, Cursor or any MCP client can query the sales data.

The client's own model writes the SQL. It goes through the same SqlTools as the Streamlit app:
validator, row limit, read-only role and statement timeout.
"""

import logging
import sys
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import psycopg
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from sqlchat.config import get_settings
from sqlchat.db import Database, describe_error
from sqlchat.schema import ALLOWED_TABLES
from sqlchat.tools import SqlTools
from sqlchat.validator import UnsafeQueryError

INSTRUCTIONS = """\
Sales data for Kopi Lereng, a fictional coffee-shop chain in Indonesia (Jan 2025 to Sep 2026).
Call describe_schema first. Then write one PostgreSQL SELECT and pass it to run_sql.
Only completed orders count as revenue. Money is in Rupiah."""

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


class SqlCheck(BaseModel):
    allowed: bool
    sql: str | None = None
    reason: str | None = None


class QueryRows(BaseModel):
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    limit_reached: bool


def build_server(tools: SqlTools) -> MCPServer:
    mcp = MCPServer("kopi-lereng-sales", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=_READ_ONLY)
    def describe_schema() -> str:
        """Tables, columns with notes on how to use them, and a few sample rows."""
        return tools.schema_text

    @mcp.tool(annotations=_READ_ONLY)
    def check_sql(sql: str) -> SqlCheck:
        """Validate a PostgreSQL query without running it.

        Returns the exact SQL that run_sql would execute, or the reason it would be rejected.
        """
        try:
            safe = tools.check_sql(sql)
        except UnsafeQueryError as exc:
            return SqlCheck(allowed=False, reason=str(exc))
        return SqlCheck(allowed=True, sql=safe.sql)

    @mcp.tool(
        annotations=_READ_ONLY,
        description=(
            "Run one read-only PostgreSQL SELECT against the sales tables and return the rows. "
            f"Results are capped at {tools.max_rows} rows; aggregate in SQL instead of paging."
        ),
    )
    def run_sql(sql: str) -> QueryRows:
        try:
            safe, result = tools.run_sql(sql)
        except UnsafeQueryError as exc:
            raise ToolError(f"Rejected by the guardrails: {exc}") from exc
        except psycopg.Error as exc:
            if exc.sqlstate is None:
                raise
            raise ToolError(f"PostgreSQL error: {describe_error(exc)}") from exc

        return QueryRows(
            sql=safe.sql,
            columns=[c.name for c in result.columns],
            rows=[[_json_value(v) for v in row] for row in result.rows],
            row_count=len(result.rows),
            limit_reached=len(result.rows) >= tools.max_rows,
        )

    return mcp


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return value


def main() -> None:
    # stdout carries the protocol; anything else has to go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    settings = get_settings()
    db = Database(settings.database_url, settings.statement_timeout_ms)
    db.ensure_read_only_role(ALLOWED_TABLES)
    build_server(SqlTools(db, settings.max_rows)).run()


if __name__ == "__main__":
    main()
