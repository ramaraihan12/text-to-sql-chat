import argparse
import sys

from sqlchat.bootstrap import setup_database
from sqlchat.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sqlchat", description="Text-to-SQL over a sales DB.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="create tables, load seed data and the read-only role")
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


if __name__ == "__main__":
    sys.exit(main())
