from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models.candle import Candle
from app.db.session import SessionLocal
from app.services.candle_ingestion import upsert_candles


def _kline(timestamp=1700000000000, close="105"):
    return [timestamp, "100", "110", "90", close, "1000"]


def test_upsert_updates_existing_row_and_deduplicates_batch():
    with SessionLocal() as db:
        rows = upsert_candles(db, "BTCUSDT", "1h", [_kline()])
        original_id = rows[0].id
        rows = upsert_candles(db, "btcusdt", "1h", [_kline(close="106"), _kline(close="107")])
        assert len(rows) == 1
        assert rows[0].id == original_id
        assert rows[0].close == 107
        assert db.query(Candle).count() == 1


def test_concurrent_writes_do_not_create_duplicates():
    def write():
        with SessionLocal() as db:
            return upsert_candles(db, "BTCUSDT", "1h", [_kline()])[0].open_time

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: write(), range(2))) == [1700000000000] * 2
    with SessionLocal() as db:
        assert db.query(Candle).count() == 1


def test_batch_failure_rolls_back_all_rows():
    with SessionLocal() as db:
        db.add(
            Candle(
                symbol="BTCUSDT",
                interval="1h",
                open_time=1,
                open=1,
                high=1,
                low=1,
                close=1,
                volume=1,
            )
        )
        db.commit()
        db.execute(
            text(
                "CREATE TRIGGER fail_second BEFORE INSERT ON candles "
                "WHEN NEW.open_time = 1700000060000 "
                "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
        )
        db.commit()
        rows = [_kline(), _kline(timestamp=1700000060000)]
        with pytest.raises(IntegrityError):
            upsert_candles(db, "BTCUSDT", "1h", rows)
        assert len(db.scalars(select(Candle)).all()) == 1
        assert len(upsert_candles(db, "BTCUSDT", "1h", [_kline()])) == 1


@pytest.mark.parametrize("rows", [[], [_kline(close="nan")], [_kline(close="inf")]])
def test_invalid_provider_data_does_not_write(rows):
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            upsert_candles(db, "BTCUSDT", "1h", rows)
        assert db.query(Candle).count() == 0


def test_http_rejects_empty_provider_data(client, monkeypatch):
    monkeypatch.setattr("app.main.fetch_klines", lambda **kwargs: [])
    response = client.post("/candles/fetch/BTCUSDT")
    assert response.status_code == 502
