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
    from uuid import uuid4

    from app.db.models.account import Account, AccountSession
    from app.db.session import SessionLocal
    from app.services import account_auth

    token = "analysis-test-session"
    with SessionLocal() as db:
        owner = str(uuid4())
        db.add(
            Account(
                id=owner,
                username="analysis_test",
                password_hash="unused-test-hash",
                active=True,
                created_ms=0,
            )
        )
        db.flush()
        db.add(
            AccountSession(
                token_hash=account_auth.digest(token), account_id=owner, expires_ms=2**62
            )
        )
        db.commit()
    with TestClient(app, base_url="https://testserver") as test_client:
        test_client.cookies.set(account_auth.COOKIE, token)
        test_client.headers["X-CSRF-Token"] = account_auth.csrf_token(token)
        yield test_client
