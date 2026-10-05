import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db.models.forward_signal import ForwardSignal
from app.services.formations import BAR
from app.services.forward_tracking import track_symbol
from tests.test_formation_history_postgres import sessions  # noqa: F401
from tests.test_forward_tracking import analysis

pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="PostgreSQL CI only")


def test_concurrent_observers_record_one_snapshot(sessions, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.services.forward_tracking.analyze", lambda *args: analysis())

    def record(_):
        with sessions() as db:
            return track_symbol(db, "BTCUSDT", 200 * BAR + 1)["recorded"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(record, [1, 2])) == [0, 1]
    with sessions() as db:
        assert db.query(ForwardSignal).count() == 1
