"""Guardrails for model-written SQL.

Only a single SELECT (or UNION/INTERSECT/EXCEPT of SELECTs) over allowlisted tables gets
through. The query that runs is the one sqlglot re-renders from the checked tree, not the raw
text, so whatever was validated is exactly what reaches Postgres.

This is the first layer, not the only one: the app also connects as a read-only role inside a
read-only transaction with a statement timeout.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

# sqlglot warns every time it falls back to an opaque Command node (EXPLAIN, DO, CALL...).
# Those are rejected below anyway, so the warning is noise in the app log.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

_WRITE_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Revoke,
    exp.Comment,
    exp.Analyze,
)

# Anything that sleeps, touches files, opens connections, changes settings or runs a query
# hidden inside a string literal.
_BLOCKED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink")
_BLOCKED_FUNCTIONS = frozenset(
    {
        "current_setting",
        "set_config",
        "nextval",
        "setval",
        "txid_current",
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "cursor_to_xml",
        "table_to_xml",
        "table_to_xml_and_xmlschema",
        "schema_to_xml",
        "database_to_xml",
    }
)


class UnsafeQueryError(ValueError):
    """The statement was rejected by the guardrails. The message says why."""


@dataclass(frozen=True)
class SafeQuery:
    sql: str
    tables: tuple[str, ...]


def validate_sql(sql: str, *, allowed_tables: Iterable[str], max_rows: int) -> SafeQuery:
    """Check a statement and return the normalized SQL with a row limit applied.

    Raises UnsafeQueryError when the statement isn't a single read-only SELECT over the
    allowed tables.
    """
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError as exc:
        raise UnsafeQueryError(f"Could not parse the SQL: {_first_line(str(exc))}") from exc

    if not statements:
        raise UnsafeQueryError("Empty query.")
    if len(statements) > 1:
        raise UnsafeQueryError("Only one statement is allowed.")

    tree = statements[0]
    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise UnsafeQueryError(f"Only SELECT queries are allowed (got {_kind(tree)}).")

    write = tree.find(*_WRITE_NODES)
    if write is not None:
        raise UnsafeQueryError(
            f"Data-modifying statements are not allowed, even inside a CTE (found {_kind(write)})."
        )
    if tree.find(exp.Into) is not None:
        raise UnsafeQueryError("SELECT ... INTO is not allowed.")
    if tree.find(exp.Lock) is not None:
        raise UnsafeQueryError("Row locking (FOR UPDATE / FOR SHARE) is not allowed.")

    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if name in _BLOCKED_FUNCTIONS or name.startswith(_BLOCKED_FUNCTION_PREFIXES):
            raise UnsafeQueryError(f"Function {name}() is not allowed.")

    tables = _check_tables(tree, frozenset(t.lower() for t in allowed_tables))
    _apply_row_limit(tree, max_rows)
    return SafeQuery(sql=tree.sql(dialect="postgres", pretty=True), tables=tables)


def _check_tables(tree: exp.Expression, allowed: frozenset[str]) -> tuple[str, ...]:
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    used: list[str] = []

    for table in tree.find_all(exp.Table):
        ident = table.this
        if not isinstance(ident, exp.Identifier):
            # FROM generate_series(...) and similar. Functions were already checked.
            continue

        # Postgres folds unquoted identifiers to lower case; quoted ones are taken literally.
        name = ident.this if ident.quoted else ident.this.lower()
        schema = table.db.lower()
        qualified = ".".join(p for p in (table.catalog, table.db, ident.this) if p)

        if table.catalog or schema not in ("", "public"):
            raise UnsafeQueryError(
                f"Table {qualified} is not available. Allowed tables: {_listing(allowed)}."
            )
        if not schema and name in cte_names:
            continue
        if name not in allowed:
            raise UnsafeQueryError(
                f"Table {qualified} is not available. Allowed tables: {_listing(allowed)}."
            )
        if name not in used:
            used.append(name)

    return tuple(used)


def _apply_row_limit(query: exp.Expression, max_rows: int) -> None:
    current = query.args.get("limit")
    if isinstance(current, exp.Limit):
        requested = _int_literal(current.expression)
    elif isinstance(current, exp.Fetch):
        requested = _int_literal(current.args.get("count"))
    else:
        requested = None

    if requested is not None and requested <= max_rows:
        return
    query.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))


def _int_literal(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return int(node.this)
        except ValueError:
            return None
    return None


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        return func.name.lower()
    return func.sql_name().lower()


def _kind(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        # EXPLAIN, CALL, DO... are kept as raw text; the keyword is the useful part.
        return str(node.this).upper()
    return node.key.upper()


def _listing(names: Iterable[str]) -> str:
    return ", ".join(sorted(names))


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text
