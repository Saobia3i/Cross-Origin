import pytest
from sqlmodel import Session

from app import db
from app import security

# Tests hash the 4 demo users over and over: use the cheapest bcrypt cost (production keeps the default).
security._pwd.update(bcrypt__rounds=4)
# A 32+ byte test secret, so PyJWT does not warn about the short development default.
from app.config import settings as _settings  # noqa: E402

_settings.jwt_secret = "test-only-secret-" + "x" * 32


@pytest.fixture
def engine(tmp_path):
    """Fresh throwaway SQLite database with all tables (never the real dhakapath.db)."""
    eng = db.make_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    db.create_db_and_tables(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(engine):
    """TestClient on a throwaway database seeded from the test fixtures (never dhakapath.db)."""
    from fastapi.testclient import TestClient

    from app.deps import get_db
    from app.main import app
    from app.services import search_cache
    from app.services.seeder import seed

    with Session(engine) as s:
        seed(s, "fixtures")

    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override
    search_cache.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()
    search_cache.clear()


@pytest.fixture
def seeded(session):
    """A session on a throwaway database seeded with the test fixtures (3 hubs, 3 edges, demo users)."""
    from app.services.seeder import seed

    seed(session, "fixtures")
    return session


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """Point report-image uploads at a temp folder."""
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    return tmp_path / "uploads"
