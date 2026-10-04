import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.candle import Candle
from app.db.session import SessionLocal


def test_unique_constraint_symbol_interval_open_time():
    db = SessionLocal()
    try:
        a = Candle(
            symbol="BTCUSDT",
            interval="1h",
            open_time=9999,
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.05,
            volume=100.0,
        )
        b = Candle(
            symbol="BTCUSDT",
            interval="1h",
            open_time=9999,  # aynı unique key
            open=1.2,
            high=1.3,
            low=1.1,
            close=1.25,
            volume=200.0,
        )

        db.add(a)
        db.commit()

        db.add(b)
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()
