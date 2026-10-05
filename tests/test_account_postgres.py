import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app.db.models.account import LoginLimit
from app.services.account_auth import digest, reserve_login
from tests.test_formation_history_postgres import sessions  # noqa: F401

pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="PostgreSQL CI only")


def test_concurrent_login_budget_is_shared(sessions):  # noqa: F811
    def attempt(_):
        with sessions() as db:
            try:
                reserve_login(db, "alice", 1000)
                return True
            except HTTPException as exc:
                assert exc.status_code == 429
                return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(10)))
    assert sum(results) == 6
    with sessions() as db:
        assert db.get(LoginLimit, digest("user:alice")).attempts == 6
