import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.formation_history import FormationEvent, FormationState
from app.services.formation_history import record_symbol
from app.services.formations import BAR
from tests.test_formation_history import analysis

pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="PostgreSQL CI only")


@pytest.fixture
def sessions():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(
            BinanceSpotSymbol(
                symbol="BTCUSDT",
                base_asset="BTC",
                active=True,
                quote_volume_24h=1,
                catalog_updated_ms=0,
            )
        )
        db.commit()
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_concurrent_initial_observation_has_one_event(sessions, monkeypatch):
    monkeypatch.setattr("app.services.formation_history.analyze", lambda *args: analysis())

    def write(_):
        with sessions() as db:
            return record_symbol(db, "BTCUSDT", 200 * BAR)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, [1, 2])) == [0, 1]
    with sessions() as db:
        assert db.query(FormationEvent).count() == 1
        assert db.query(FormationState).count() == 1


def test_failed_history_write_rolls_back_both_tables(sessions, monkeypatch):
    from app.services import formation_history

    monkeypatch.setattr(formation_history, "analyze", lambda *args: analysis())
    original = formation_history.save_analysis

    def fail(db, data, stamp):
        original(db, data, stamp)
        db.flush()
        raise RuntimeError("simulated history failure")

    monkeypatch.setattr(formation_history, "save_analysis", fail)
    with sessions() as db:
        with pytest.raises(RuntimeError):
            record_symbol(db, "BTCUSDT", 200 * BAR)
        assert db.query(FormationEvent).count() == 0
        assert db.query(FormationState).count() == 0
