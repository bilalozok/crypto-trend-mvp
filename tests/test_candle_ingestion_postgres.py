import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models.candle import Candle
from app.services.candle_ingestion import upsert_candles

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is only configured in PostgreSQL CI",
)


@pytest.fixture
def postgres_sessions():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_postgres_concurrent_upsert(postgres_sessions):
    def write(close):
        with postgres_sessions() as db:
            return upsert_candles(
                db, "BTCUSDT", "1h", [[1700000000000, "100", "110", "90", close, "1000"]]
            )[0].open_time

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(write, ["105", "106"])) == [1700000000000] * 2
    with postgres_sessions() as db:
        assert db.query(Candle).count() == 1
        assert db.query(Candle).first().close in (105, 106)


def test_postgres_failed_batch_is_atomic(postgres_sessions):
    with postgres_sessions() as db:
        db.execute(text("ALTER TABLE candles ADD CONSTRAINT positive_close CHECK (close > 0)"))
        db.commit()
        with pytest.raises(IntegrityError):
            upsert_candles(
                db,
                "BTCUSDT",
                "1h",
                [
                    [1700000000000, "100", "110", "90", "105", "1000"],
                    [1700000060000, "100", "110", "90", "-1", "1000"],
                ],
            )
        assert db.query(Candle).count() == 0
        assert (
            len(
                upsert_candles(
                    db, "BTCUSDT", "1h", [[1700000000000, "100", "110", "90", "105", "1000"]]
                )
            )
            == 1
        )
