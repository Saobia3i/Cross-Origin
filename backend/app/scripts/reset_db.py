"""Delete the database and recreate empty tables.

    python -m app.scripts.reset_db
"""
import sys

from app import db


def main() -> int:
    print(f"reset: {db.reset_db()} ({db.DATABASE_URL})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
