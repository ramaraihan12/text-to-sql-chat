"""Runs against a real Postgres. Skipped unless TEST_ADMIN_DATABASE_URL and TEST_DATABASE_URL
are set; the setup step rebuilds the tables in that database, so point it at a scratch DB."""

import os

import psycopg
import pytest

from sqlchat.bootstrap import setup_database
from sqlchat.db import Database
from sqlchat.pipeline import TextToSql
from sqlchat.schema import ALLOWED_TABLES, load_schema
from sqlchat.seed import generate
from sqlchat.tools import SqlTools
from sqlchat.validator import UnsafeQueryError
from tests.fakes import scripted_model

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
READER_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (ADMIN_URL and READER_URL),
        reason="set TEST_ADMIN_DATABASE_URL and TEST_DATABASE_URL to run",
    ),
]


@pytest.fixture(scope="module")
def counts() -> dict[str, int]:
    assert ADMIN_URL and READER_URL
    return setup_database(ADMIN_URL, READER_URL, statement_timeout_ms=3000)


@pytest.fixture
def reader(counts: dict[str, int]) -> Database:
    assert READER_URL
    return Database(READER_URL, statement_timeout_ms=3000)


def test_seed_lands_in_postgres(counts: dict[str, int], reader: Database) -> None:
    data = generate()
    assert counts["orders"] == len(data.orders)
    result = reader.query("SELECT count(*) FROM order_items")
    assert result.rows[0][0] == len(data.order_items)


def test_setup_can_run_twice(counts: dict[str, int]) -> None:
    assert ADMIN_URL and READER_URL
    assert setup_database(ADMIN_URL, READER_URL, statement_timeout_ms=3000) == counts


def test_reader_role_passes_the_startup_check(reader: Database) -> None:
    reader.ensure_read_only_role(ALLOWED_TABLES)


def test_owner_role_fails_the_startup_check(counts: dict[str, int]) -> None:
    assert ADMIN_URL
    with pytest.raises(PermissionError):
        Database(ADMIN_URL).ensure_read_only_role(ALLOWED_TABLES)


# The next three skip the validator on purpose: the database has to hold the line on its own.


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM orders",
        "UPDATE products SET price_idr = 1",
        "CREATE TABLE sneaky (id int)",
    ],
)
def test_role_cannot_write_even_without_the_validator(reader: Database, statement: str) -> None:
    refused = (psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege)
    with pytest.raises(refused):
        reader.query(statement)


def test_role_cannot_read_other_tables_in_the_schema(counts: dict[str, int]) -> None:
    assert ADMIN_URL and READER_URL
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute("CREATE TABLE IF NOT EXISTS staff_salaries (name text, salary_idr bigint)")
        try:
            # Run setup again with the extra table in place, so a too-broad grant would show.
            setup_database(ADMIN_URL, READER_URL, statement_timeout_ms=3000)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                Database(READER_URL).query("SELECT * FROM staff_salaries")
        finally:
            admin.execute("DROP TABLE staff_salaries")


def test_statement_timeout_stops_slow_queries(counts: dict[str, int]) -> None:
    assert READER_URL
    with pytest.raises(psycopg.errors.QueryCanceled):
        Database(READER_URL, statement_timeout_ms=200).query("SELECT pg_sleep(2)")


def test_catalog_views_are_readable_so_the_validator_has_to_stop_them(reader: Database) -> None:
    # Postgres lets every role read pg_roles, pg_settings and friends. The grants don't help.
    assert reader.query("SELECT count(*) FROM pg_roles").rows[0][0] > 0

    with pytest.raises(UnsafeQueryError, match="pg_roles"):
        SqlTools(reader, max_rows=50).run_sql(
            "SELECT * FROM (WITH pg_roles AS (SELECT 1 AS x) SELECT * FROM pg_roles) a "
            "CROSS JOIN pg_roles"
        )


def test_schema_context_has_notes_and_samples(reader: Database) -> None:
    tables = {t.name: t for t in load_schema(reader)}
    assert set(tables) == set(ALLOWED_TABLES)
    assert "completed" in tables["orders"].description
    notes = {c.name: c.description for c in tables["order_items"].columns}
    assert "time of sale" in notes["unit_price_idr"]
    assert len(tables["stores"].sample_rows) == 3


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT date_trunc('month', ordered_at) AS month, count(*) FROM orders "
        "WHERE ordered_at >= now() - interval '2 years' GROUP BY 1 ORDER BY 1",
        "SELECT to_char(ordered_at, 'YYYY-MM') AS month, count(*) FROM orders GROUP BY 1",
        "SELECT extract(hour FROM ordered_at) AS hour, count(*) FROM orders GROUP BY 1 ORDER BY 2",
        "SELECT count(*) FILTER (WHERE channel = 'delivery') AS delivery FROM orders",
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY unit_price_idr) FROM order_items",
        "SELECT string_agg(DISTINCT city, ', ') FROM stores",
        "SELECT name, rank() OVER (ORDER BY price_idr DESC) FROM products",
        "SELECT d::date AS day FROM generate_series('2026-09-01'::date, '2026-09-07', "
        "interval '1 day') AS d",
        "SELECT s.name, round(avg(t.total), 0) AS avg_ticket FROM stores s JOIN "
        "(SELECT o.store_id, sum(i.quantity * i.unit_price_idr) AS total FROM orders o "
        "JOIN order_items i ON i.order_id = o.id WHERE o.status = 'completed' "
        "GROUP BY o.id, o.store_id) t ON t.store_id = s.id GROUP BY s.name ORDER BY 2 DESC",
        "SELECT city FROM customers INTERSECT SELECT city FROM stores",
        "SELECT name FROM products WHERE name ILIKE '%kopi%'",
    ],
)
def test_rewritten_sql_still_runs_on_postgres(reader: Database, sql: str) -> None:
    """The validator executes sqlglot's re-rendered SQL, so it has to stay valid Postgres."""
    _, result = SqlTools(reader, max_rows=50).run_sql(sql)
    assert result.rows


def test_pipeline_recovers_from_a_real_postgres_error(reader: Database) -> None:
    model = scripted_model(
        "```sql\nSELECT city, count(*) AS orders FROM orders GROUP BY city\n```",
        "```sql\nSELECT s.city, count(*) AS orders FROM orders o "
        "JOIN stores s ON s.id = o.store_id GROUP BY s.city ORDER BY orders DESC\n```",
        "Jakarta has the most orders.",
    )

    answer = TextToSql(model, SqlTools(reader, max_rows=50)).ask("Orders per city?")

    assert answer.status == "ok"
    assert answer.attempts[0].error is not None
    assert 'column "city" does not exist' in answer.attempts[0].error
    assert answer.result is not None
    assert answer.result.rows[0][0] == "Jakarta"
