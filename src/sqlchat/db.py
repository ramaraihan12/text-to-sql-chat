"""Read-only access to Postgres."""

from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from psycopg.abc import Params, Query

ColumnKind = Literal["number", "time", "text"]

# Built-in type OIDs, used to decide how a result column can be charted.
_NUMBER_OIDS = {20, 21, 23, 700, 701, 1700}  # int8, int2, int4, float4, float8, numeric
_TIME_OIDS = {1082, 1114, 1184}  # date, timestamp, timestamptz


@dataclass(frozen=True)
class Column:
    name: str
    kind: ColumnKind


@dataclass(frozen=True)
class QueryResult:
    columns: list[Column]
    rows: list[tuple[Any, ...]]


class Database:
    def __init__(self, dsn: str, statement_timeout_ms: int = 5000) -> None:
        self._dsn = dsn
        self._options = f"-c statement_timeout={statement_timeout_ms} -c timezone=Asia/Jakarta"

    def query(self, query: Query, params: Params | None = None) -> QueryResult:
        """Run one statement in a read-only transaction and return every row."""
        with psycopg.connect(self._dsn, options=self._options, connect_timeout=5) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(query, params)
                if cur.description is None:
                    return QueryResult(columns=[], rows=[])
                columns = [Column(d.name, _kind(d.type_code)) for d in cur.description]
                return QueryResult(columns=columns, rows=cur.fetchall())

    def ensure_read_only_role(self, tables: tuple[str, ...]) -> None:
        """Refuse to run as a role that could change data.

        The validator should already stop writes, but if DATABASE_URL is pointed at an owner
        or superuser account by mistake, it's better to fail loudly at startup.
        """
        result = self.query(
            """
            SELECT r.rolsuper,
                   bool_or(has_table_privilege(current_user, t.name,
                           'INSERT, UPDATE, DELETE, TRUNCATE'))
            FROM pg_roles r, unnest(%s::text[]) AS t(name)
            WHERE r.rolname = current_user
            GROUP BY r.rolsuper
            """,
            (list(tables),),
        )
        is_super, can_write = result.rows[0]
        if is_super or can_write:
            raise PermissionError(
                "DATABASE_URL connects as a role that can modify data. "
                "Point it at the read-only role created by `sqlchat setup`."
            )


def _kind(type_oid: int) -> ColumnKind:
    if type_oid in _NUMBER_OIDS:
        return "number"
    if type_oid in _TIME_OIDS:
        return "time"
    return "text"
