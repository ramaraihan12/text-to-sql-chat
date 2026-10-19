"""Guardrails for model-written SQL.

Only a single SELECT (or UNION/INTERSECT/EXCEPT of SELECTs) over allowlisted tables gets
through. The query that runs is the one sqlglot re-renders from the checked tree, not the raw
text, so whatever was validated is exactly what reaches Postgres.

For writes this is the first layer, not the only one: the app also connects as a read-only role
inside a read-only transaction with a statement timeout. Reads are different. Every Postgres
role can read catalog views like pg_roles and pg_settings, so keeping queries on the sales
tables is up to this module.
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

# Functions sqlglot has a class for are the standard aggregate, window, date, string and math
# ones. Postgres-only functions come back as exp.Anonymous and must be on this list, so pg_sleep,
# pg_read_file, current_setting, dblink, query_to_xml, ts_stat and whatever else sleeps, reads
# files or runs a query from a string literal are rejected without having to name them all.
_ALLOWED_PG_FUNCTIONS = frozenset(
    {
        # dates and times
        "age",
        "clock_timestamp",
        "date_subtract",
        "isfinite",
        "make_date",
        "make_timestamptz",
        "statement_timestamp",
        "timezone",
        "transaction_timestamp",
        # numbers and text
        "gcd",
        "lcm",
        "num_nonnulls",
        "num_nulls",
        "octet_length",
        "regexp_matches",
        "regexp_split_to_array",
        "regexp_split_to_table",
        "scale",
        "trim_scale",
        # aggregates, arrays and json
        "cardinality",
        "every",
        "json_array_elements",
        "json_build_array",
        "json_build_object",
        "jsonb_agg",
        "jsonb_array_elements",
        "jsonb_build_array",
        "jsonb_build_object",
        "row_to_json",
        "to_json",
        "to_jsonb",
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

    for func in tree.find_all(exp.Anonymous):
        name = func.name.lower()
        if name not in _ALLOWED_PG_FUNCTIONS:
            raise UnsafeQueryError(f"Function {name}() is not allowed.")

    tables = _check_tables(tree, frozenset(t.lower() for t in allowed_tables))
    _apply_row_limit(tree, max_rows)
    return SafeQuery(sql=tree.sql(dialect="postgres", pretty=True), tables=tables)


def _check_tables(tree: exp.Expression, allowed: frozenset[str]) -> tuple[str, ...]:
    used: list[str] = []

    for table in tree.find_all(exp.Table):
        ident = table.this
        if not isinstance(ident, exp.Identifier):
            # FROM generate_series(...) and similar. Functions were already checked.
            continue

        name = _pg_name(ident)
        schema = table.db.lower()
        qualified = ".".join(p for p in (table.catalog, table.db, ident.this) if p)

        if table.catalog or schema not in ("", "public"):
            raise UnsafeQueryError(
                f"Table {qualified} is not available. Allowed tables: {_listing(allowed)}."
            )
        if not schema and name in _ctes_in_scope(table):
            continue
        if name not in allowed:
            raise UnsafeQueryError(
                f"Table {qualified} is not available. Allowed tables: {_listing(allowed)}."
            )
        if name not in used:
            used.append(name)

    return tuple(used)


def _ctes_in_scope(table: exp.Table) -> set[str]:
    """CTE names this table reference can resolve to, following Postgres' scoping rules.

    A CTE is visible in the query that owns its WITH (subqueries included) and in the CTEs after
    it in the same list. Under WITH RECURSIVE every CTE in the list sees all of them, itself too.
    A CTE defined anywhere else doesn't count: Postgres resolves the name to a real relation,
    which for a name like pg_roles is a catalog view every role can read.
    """
    names: set[str] = set()
    child: exp.Expression = table
    parent = table.parent
    while parent is not None:
        if isinstance(parent, exp.With):
            ctes = parent.expressions
            if not parent.args.get("recursive"):
                ctes = ctes[: next((i for i, cte in enumerate(ctes) if cte is child), 0)]
            names.update(_cte_name(cte) for cte in ctes)
        elif isinstance(parent, exp.Query) and not isinstance(child, exp.With):
            names.update(_cte_name(cte) for cte in parent.ctes)
        child, parent = parent, parent.parent
    return names


def _cte_name(cte: exp.CTE) -> str:
    return _pg_name(cte.args["alias"].this)


def _pg_name(ident: exp.Identifier) -> str:
    # Postgres folds unquoted identifiers to lower case; quoted ones are taken literally.
    return ident.this if ident.quoted else ident.this.lower()


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


def _kind(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        # EXPLAIN, CALL, DO... are kept as raw text; the keyword is the useful part.
        return str(node.this).upper()
    return node.key.upper()


def _listing(names: Iterable[str]) -> str:
    return ", ".join(sorted(names))


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text
