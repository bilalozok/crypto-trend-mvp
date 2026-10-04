import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Test DB dosyası (geçici)
tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"

# app importu ENV set edildikten sonra
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def prepare_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session", autouse=True)
def cleanup_db_file():
    yield
    engine.dispose()
    tmp_db.close()
    try:
        os.unlink(tmp_db.name)
    except Exception:
        pass


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
