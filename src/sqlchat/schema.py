"""What the model gets to see about the database: tables, column notes and a few sample rows."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import sql

from sqlchat.db import Database

ALLOWED_TABLES = ("stores", "products", "customers", "orders", "order_items")
SAMPLE_ROWS = 3


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    description: str


@dataclass(frozen=True)
class TableInfo:
    name: str
    description: str
    columns: list[ColumnInfo]
    sample_rows: list[tuple[Any, ...]]


def load_schema(db: Database, tables: tuple[str, ...] = ALLOWED_TABLES) -> list[TableInfo]:
    """Read table and column comments from the catalog, plus a few rows per table."""
    catalog = db.query(
        """
        SELECT c.relname,
               coalesce(obj_description(c.oid, 'pg_class'), ''),
               a.attname,
               format_type(a.atttypid, a.atttypmod),
               coalesce(col_description(c.oid, a.attnum), '')
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname = 'public' AND c.relname = ANY(%s)
        ORDER BY array_position(%s, c.relname::text), a.attnum
        """,
        (list(tables), list(tables)),
    )

    by_table: dict[str, tuple[str, list[ColumnInfo]]] = {}
    for table, table_note, column, data_type, column_note in catalog.rows:
        _, columns = by_table.setdefault(table, (table_note, []))
        columns.append(ColumnInfo(column, data_type, column_note))

    result = []
    for table, (note, columns) in by_table.items():
        sample = db.query(
            sql.SQL("SELECT * FROM {} ORDER BY 1 LIMIT {}").format(
                sql.Identifier(table), sql.Literal(SAMPLE_ROWS)
            )
        )
        result.append(TableInfo(table, note, columns, sample.rows))
    return result


def render_schema(tables: list[TableInfo]) -> str:
    """Plain-text schema description used in prompts and returned by the MCP server."""
    blocks = []
    for table in tables:
        lines = [f"TABLE {table.name}" + (f" -- {table.description}" if table.description else "")]
        for col in table.columns:
            note = f" -- {col.description}" if col.description else ""
            lines.append(f"  {col.name} {col.data_type}{note}")
        if table.sample_rows:
            lines.append("  sample rows:")
            header = " | ".join(c.name for c in table.columns)
            lines.append(f"    {header}")
            lines.extend(f"    {' | '.join(_cell(v) for v in row)}" for row in table.sample_rows)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)
