"""One-off database setup: tables, seed rows and the read-only role the app connects as."""

from importlib.resources import files

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from sqlchat.schema import ALLOWED_TABLES
from sqlchat.seed import SeedData, generate

_COLUMNS = {
    "stores": ("id", "name", "city", "region", "opened_on"),
    "products": ("id", "name", "category", "price_idr"),
    "customers": ("id", "full_name", "city", "joined_on", "loyalty_tier"),
    "orders": (
        "id",
        "store_id",
        "customer_id",
        "ordered_at",
        "channel",
        "payment_method",
        "status",
    ),
    "order_items": ("id", "order_id", "product_id", "quantity", "unit_price_idr"),
}


def setup_database(admin_url: str, reader_url: str, statement_timeout_ms: int) -> dict[str, int]:
    """Recreate the schema, load the seed data and (re)create the reader role.

    Safe to run more than once: tables are dropped and rebuilt with the same rows.
    Returns the row count per table.
    """
    reader = conninfo_to_dict(reader_url)
    admin = conninfo_to_dict(admin_url)
    role, password = reader.get("user"), reader.get("password")
    if not role or not password:
        raise ValueError("DATABASE_URL needs a user name and password for the read-only role.")
    if role == admin.get("user"):
        raise ValueError(
            "DATABASE_URL and ADMIN_DATABASE_URL use the same user. "
            "The app must connect as its own read-only role."
        )

    data = generate()
    with psycopg.connect(admin_url) as conn:
        conn.execute(files("sqlchat").joinpath("sql/schema.sql").read_text(encoding="utf-8"))
        _load(conn, data)
        _create_reader_role(conn, str(role), str(password), statement_timeout_ms)

    return {table: len(getattr(data, table)) for table in _COLUMNS}


def _load(conn: psycopg.Connection, data: SeedData) -> None:
    with conn.cursor() as cur:
        for table, columns in _COLUMNS.items():
            copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
            )
            with cur.copy(copy_sql) as copy:
                for row in getattr(data, table):
                    copy.write_row(row)
        cur.execute("ANALYZE")


def _create_reader_role(
    conn: psycopg.Connection, role: str, password: str, statement_timeout_ms: int
) -> None:
    db_name = conn.info.dbname
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    ident = sql.Identifier(role)
    db = sql.Identifier(db_name)

    conn.execute(
        sql.SQL(
            "{} ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
        ).format(sql.SQL("ALTER" if exists else "CREATE"), ident, sql.Literal(password))
    )

    # Belt and braces: even if a query slipped past the validator, sessions for this role
    # start read-only and get cut off after the timeout.
    for setting, value in (
        ("default_transaction_read_only", "on"),
        ("statement_timeout", str(statement_timeout_ms)),
        ("timezone", "Asia/Jakarta"),
    ):
        conn.execute(
            sql.SQL("ALTER ROLE {} IN DATABASE {} SET {} = {}").format(
                ident, db, sql.Identifier(setting), sql.Literal(value)
            )
        )

    tables = sql.SQL(", ").join(map(sql.Identifier, ALLOWED_TABLES))
    for statement in (
        sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"),
        sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(ident),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, ident),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident),
        sql.SQL("GRANT SELECT ON {} TO {}").format(tables, ident),
    ):
        conn.execute(statement)
