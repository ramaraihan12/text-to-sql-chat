import pytest

from sqlchat.validator import UnsafeQueryError, validate_sql

TABLES = ("stores", "products", "customers", "orders", "order_items")


def check(sql: str, max_rows: int = 100):
    return validate_sql(sql, allowed_tables=TABLES, max_rows=max_rows)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM orders",
        "select name, city from stores where city = 'Bandung'",
        "SELECT * FROM ORDERS",
        "SELECT * FROM public.orders",
        "SELECT s.city, sum(i.quantity * i.unit_price_idr) AS revenue "
        "FROM orders o JOIN stores s ON s.id = o.store_id "
        "JOIN order_items i ON i.order_id = o.id GROUP BY s.city ORDER BY revenue DESC",
        "WITH monthly AS (SELECT date_trunc('month', ordered_at) AS m, count(*) AS n "
        "FROM orders GROUP BY 1) SELECT * FROM monthly ORDER BY m",
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5) "
        "SELECT * FROM r",
        "SELECT name FROM products UNION SELECT name FROM stores",
        "SELECT city FROM customers INTERSECT SELECT city FROM stores",
        "SELECT city FROM customers EXCEPT SELECT city FROM stores",
        "SELECT * FROM orders o WHERE EXISTS "
        "(SELECT 1 FROM customers c WHERE c.id = o.customer_id)",
        "SELECT to_char(ordered_at, 'YYYY-MM') AS month, count(*) FROM orders GROUP BY 1",
        "SELECT extract(dow FROM ordered_at) AS dow, count(*) FROM orders GROUP BY 1",
        "SELECT count(*) FILTER (WHERE channel = 'delivery') FROM orders",
        "SELECT string_agg(name, ', ') FROM products",
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY unit_price_idr) FROM order_items",
        "SELECT ordered_at::date AS day, count(*) FROM orders GROUP BY 1",
        "SELECT d::date FROM generate_series('2026-01-01'::date, '2026-01-07', '1 day') AS d",
        "SELECT name, rank() OVER (ORDER BY price_idr DESC) FROM products",
        "SELECT * FROM orders WHERE ordered_at >= now() - interval '30 days'",
        "SELECT 1;",
        "-- top products\nSELECT name FROM products",
    ],
)
def test_accepts_read_only_selects(sql: str) -> None:
    result = check(sql)
    assert result.sql.upper().lstrip().startswith(("SELECT", "WITH", "/*"))


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("", "Empty query"),
        ("   ", "Empty query"),
        (";", "Empty query"),
        ("INSERT INTO orders (id) VALUES (1)", "Only SELECT"),
        ("UPDATE products SET price_idr = 0", "Only SELECT"),
        ("DELETE FROM orders", "Only SELECT"),
        ("DROP TABLE orders", "Only SELECT"),
        ("TRUNCATE orders", "Only SELECT"),
        ("ALTER TABLE orders ADD COLUMN x int", "Only SELECT"),
        ("CREATE TABLE x AS SELECT * FROM orders", "Only SELECT"),
        ("GRANT ALL ON orders TO public", "Only SELECT"),
        ("COPY orders TO '/tmp/orders.csv'", "Only SELECT"),
        ("SET statement_timeout = 0", "Only SELECT"),
        ("BEGIN", "Only SELECT"),
        ("EXPLAIN ANALYZE SELECT * FROM orders", "Only SELECT"),
        ("CALL refresh_everything()", "Only SELECT"),
        ("DO $$ BEGIN DELETE FROM orders; END $$", "Only SELECT"),
        ("VALUES (1), (2)", "Only SELECT"),
        ("SELECT 1; SELECT 2", "Only one statement"),
        ("SELECT * FROM orders; DROP TABLE orders", "Only one statement"),
        ("SELECT 1 -- harmless\n; DELETE FROM orders", "Only one statement"),
        (
            "WITH gone AS (DELETE FROM orders RETURNING *) SELECT count(*) FROM gone",
            "Data-modifying",
        ),
        (
            "WITH moved AS (INSERT INTO stores (id) VALUES (99) RETURNING id) SELECT * FROM moved",
            "Data-modifying",
        ),
        (
            "WITH x AS (UPDATE products SET price_idr = 1 RETURNING id) SELECT * FROM x",
            "Data-modifying",
        ),
        ("SELECT * INTO orders_backup FROM orders", "INTO"),
        ("SELECT * FROM orders FOR UPDATE", "Row locking"),
        ("SELECT * FROM orders FOR SHARE", "Row locking"),
        ("SELECT pg_sleep(10)", "pg_sleep"),
        ("SELECT PG_SLEEP(10)", "pg_sleep"),
        ("SELECT pg_catalog.pg_sleep(1)", "pg_sleep"),
        ("SELECT * FROM orders WHERE pg_sleep(1) IS NOT NULL", "pg_sleep"),
        ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
        ("SELECT pg_terminate_backend(1)", "pg_terminate_backend"),
        ("SELECT lo_import('/etc/passwd')", "lo_import"),
        ("SELECT dblink('host=evil', 'select 1')", "dblink"),
        ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
        ("SELECT current_setting('data_directory')", "current_setting"),
        ("SELECT nextval('orders_id_seq')", "nextval"),
        ("SELECT query_to_xml('delete from orders', true, true, '')", "query_to_xml"),
        ("SELECT * FROM users", "Table users is not available"),
        ("SELECT * FROM pg_catalog.pg_user", "pg_catalog.pg_user"),
        ("SELECT * FROM pg_shadow", "pg_shadow"),
        ("SELECT * FROM information_schema.tables", "information_schema.tables"),
        ("SELECT * FROM other_schema.orders", "other_schema.orders"),
        ('SELECT * FROM "Orders"', "Table Orders is not available"),
        (
            "SELECT * FROM orders WHERE customer_id IN (SELECT usesysid FROM pg_user)",
            "pg_user",
        ),
        ("SELECT * FROM orders WHERE", "Could not parse"),
    ],
)
def test_rejects(sql: str, reason: str) -> None:
    with pytest.raises(UnsafeQueryError, match=reason):
        check(sql)


def test_cte_name_does_not_need_allowlisting() -> None:
    result = check("WITH top AS (SELECT * FROM products) SELECT * FROM top")
    assert result.tables == ("products",)


def test_schema_qualified_cte_name_is_still_checked() -> None:
    with pytest.raises(UnsafeQueryError, match="secret"):
        check("WITH secret AS (SELECT 1) SELECT * FROM public.secret")


def test_reports_tables_used_once_each() -> None:
    result = check(
        "SELECT * FROM orders o JOIN order_items i ON i.order_id = o.id "
        "WHERE o.id IN (SELECT order_id FROM order_items)"
    )
    assert result.tables == ("orders", "order_items")


def test_adds_limit_when_missing() -> None:
    assert check("SELECT * FROM orders", max_rows=50).sql.endswith("LIMIT 50")


def test_keeps_smaller_limit() -> None:
    sql = check("SELECT * FROM orders LIMIT 5", max_rows=50).sql
    assert sql.endswith("LIMIT 5")
    assert "LIMIT 50" not in sql


def test_clamps_larger_limit() -> None:
    sql = check("SELECT * FROM orders LIMIT 100000", max_rows=50).sql
    assert sql.endswith("LIMIT 50")
    assert "100000" not in sql


def test_replaces_limit_all() -> None:
    assert check("SELECT * FROM orders LIMIT ALL", max_rows=50).sql.endswith("LIMIT 50")


def test_replaces_non_literal_limit() -> None:
    sql = check("SELECT * FROM orders LIMIT (SELECT count(*) FROM orders)", max_rows=50).sql
    assert sql.endswith("LIMIT 50")


def test_keeps_offset() -> None:
    sql = check("SELECT * FROM orders ORDER BY id LIMIT 10 OFFSET 20", max_rows=50).sql
    assert "OFFSET 20" in sql
    assert "LIMIT 10" in sql


def test_fetch_first_within_limit_is_kept() -> None:
    sql = check("SELECT * FROM orders FETCH FIRST 3 ROWS ONLY", max_rows=50).sql
    assert "FETCH FIRST 3 ROWS ONLY" in sql


def test_fetch_first_over_limit_becomes_limit() -> None:
    sql = check("SELECT * FROM orders FETCH FIRST 900 ROWS ONLY", max_rows=50).sql
    assert sql.endswith("LIMIT 50")
    assert "FETCH" not in sql


def test_limit_applies_to_whole_union() -> None:
    sql = check("SELECT name FROM products UNION ALL SELECT name FROM stores", max_rows=7).sql
    assert sql.endswith("LIMIT 7")
    assert sql.count("LIMIT") == 1


def test_output_is_re_rendered_not_raw_input() -> None:
    sql = check("select   name\nfrom products /* hi */ where price_idr>30000").sql
    assert sql.startswith("SELECT")
    assert "price_idr > 30000" in sql
