"""The guarded operations shared by the chat pipeline and the MCP server.

Everything that touches the database from model input goes through here, so the Streamlit app
and an MCP client like Claude Desktop get exactly the same checks.
"""

from functools import cached_property

from sqlchat.db import Database, QueryResult
from sqlchat.schema import ALLOWED_TABLES, load_schema, render_schema
from sqlchat.validator import SafeQuery, validate_sql


class SqlTools:
    def __init__(
        self, db: Database, max_rows: int, tables: tuple[str, ...] = ALLOWED_TABLES
    ) -> None:
        self._db = db
        self.max_rows = max_rows
        self.tables = tables

    @cached_property
    def schema_text(self) -> str:
        """Tables, column notes and sample rows, loaded once per process."""
        return render_schema(load_schema(self._db, self.tables))

    def check_sql(self, sql: str) -> SafeQuery:
        """Validate without running. Raises UnsafeQueryError with the reason."""
        return validate_sql(sql, allowed_tables=self.tables, max_rows=self.max_rows)

    def run_sql(self, sql: str) -> tuple[SafeQuery, QueryResult]:
        """Validate, then run the normalized SQL. Postgres errors are left to the caller."""
        safe = self.check_sql(sql)
        return safe, self._db.query(safe.sql)
