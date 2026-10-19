import argparse
import sys

from sqlchat.bootstrap import setup_database
from sqlchat.config import Settings, get_settings
from sqlchat.db import Database, QueryResult
from sqlchat.llm import build_chat_model
from sqlchat.pipeline import TextToSql
from sqlchat.schema import ALLOWED_TABLES
from sqlchat.tools import SqlTools


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sqlchat", description="Text-to-SQL over a sales DB.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="create tables, load seed data and the read-only role")
    ask = commands.add_parser("ask", help="ask one question and print the SQL and answer")
    ask.add_argument("question")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.command == "setup":
        counts = setup_database(
            settings.admin_database_url, settings.database_url, settings.statement_timeout_ms
        )
        for table, count in counts.items():
            print(f"{table:<12} {count:>6} rows")
        print("read-only role ready")
        return 0

    answer = build_pipeline(settings).ask(args.question)
    if answer.sql:
        print(answer.sql, end="\n\n")
    if answer.result is not None:
        print(_format_table(answer.result), end="\n\n")
    print(answer.text)
    return 0 if answer.status == "ok" else 1


def build_pipeline(settings: Settings) -> TextToSql:
    db = Database(settings.database_url, settings.statement_timeout_ms)
    db.ensure_read_only_role(ALLOWED_TABLES)
    return TextToSql(build_chat_model(settings), SqlTools(db, settings.max_rows))


def _format_table(result: QueryResult, limit: int = 15) -> str:
    header = [c.name for c in result.columns]
    rows = [["NULL" if v is None else str(v) for v in row] for row in result.rows[:limit]]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(header)]
    lines = [
        "  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True)),
        "  ".join("-" * w for w in widths),
        *("  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)) for r in rows),
    ]
    if len(result.rows) > limit:
        lines.append(f"... {len(result.rows) - limit} more rows")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
