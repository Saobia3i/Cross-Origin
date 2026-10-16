"""SQLModel engine, table creation, reset and session dependency.

Relative sqlite paths resolve against backend/ (default backend/data/dhakapath.db).
"""
import sqlite3
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import BASE_DIR, settings


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, _record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def _resolve_url(url: str) -> str:
    # Hosts (Render, Heroku) hand out "postgres://" / "postgresql://"; pin the psycopg2 driver we install.
    for bare in ("postgres://", "postgresql://"):
        if url.startswith(bare):
            url = "postgresql+psycopg2://" + url[len(bare):]
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////"):
        path = url[len(prefix):]
        if path and path != ":memory:" and not (len(path) > 1 and path[1] == ":"):
            abs_path = (BASE_DIR / path).resolve()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return prefix + abs_path.as_posix()
    return url


def make_engine(url: str):
    url = _resolve_url(url)
    return create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})


DATABASE_URL = _resolve_url(settings.database_url)
engine = make_engine(DATABASE_URL)


def sqlite_file(eng=None) -> Path | None:
    """Path of the sqlite file behind an engine, or None for in-memory / non-sqlite."""
    eng = eng or engine
    if eng.dialect.name != "sqlite" or not eng.url.database or eng.url.database == ":memory:":
        return None
    return Path(eng.url.database)


def create_db_and_tables(eng=None) -> None:
    import app.models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(eng or engine)


def reset_db(eng=None) -> str:
    """Delete the sqlite file (or drop all tables) and recreate empty tables. Returns what was done."""
    eng = eng or engine
    import app.models  # noqa: F401

    path = sqlite_file(eng)
    eng.dispose()
    how = "dropped and recreated tables"
    if path is not None and path.exists():
        try:
            path.unlink()
            how = f"deleted {path.name}"
        except PermissionError:  # e.g. the file is open in a running server
            SQLModel.metadata.drop_all(eng)
    else:
        SQLModel.metadata.drop_all(eng)
    create_db_and_tables(eng)
    return how


def get_session():
    with Session(engine) as session:
        yield session
