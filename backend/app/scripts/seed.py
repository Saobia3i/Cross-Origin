"""Seed the database. Idempotent.

    python -m app.scripts.seed --source normalized          # production: data/generated/normalized_*.json
    python -m app.scripts.seed --source fixtures --reset    # synthetic test fixtures (test-only)

--reset deletes the SQLite file (or drops all tables) first. Switching between 'normalized' and
'fixtures' requires --reset. Without normalized files only demo users and default fare rules are seeded.
"""
from __future__ import annotations

import argparse
import sys

from sqlmodel import Session

from app import db
from app.services.seeder import SOURCES, format_result, seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--reset", action="store_true", help="wipe the database first")
    args = parser.parse_args(argv)

    if args.reset:
        print(f"reset: {db.reset_db()}")
    else:
        db.create_db_and_tables()
    try:
        with Session(db.engine) as session:
            result = seed(session, args.source)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_result(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
