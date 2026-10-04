from datetime import datetime

import pytest

from app.db.models.candle import Candle
from app.db.session import SessionLocal

TIMES = [1700000000000 + i * 60000 for i in range(6)]


@pytest.fixture
def seeded_candles(client):
    with SessionLocal() as db:
        for symbol, interval, times in (
            ("BTCUSDT", "1m", TIMES),
            ("ETHUSDT", "1m", TIMES),
            ("BTCUSDT", "1h", TIMES),
        ):
            db.add_all(
                Candle(
                    symbol=symbol,
                    interval=interval,
                    open_time=t,
                    open=100,
                    high=110,
                    low=90,
                    close=105,
                    volume=1000,
                )
                for t in times
            )
        db.commit()
    return client


def _times(response):
    assert response.status_code == 200, response.text
    return [
        int(datetime.fromisoformat(row["open_time"].replace("Z", "+00:00")).timestamp() * 1000)
        for row in response.json()
    ]


def test_latest_supports_sort_asc_desc(seeded_candles):
    client = seeded_candles
    url = "/candles/latest?symbol=btcusdt&interval=1m&limit=3"
    assert _times(client.get(url)) == TIMES[-3:]
    assert _times(client.get(url + "&sort=asc")) == TIMES[-3:]
    assert _times(client.get(url + "&sort=desc")) == list(reversed(TIMES[-3:]))
    rows = client.get(url).json()
    assert all(row["symbol"] == "BTCUSDT" and row["interval"] == "1m" for row in rows)


def test_latest_supports_limit_offset(seeded_candles):
    client = seeded_candles
    url = "/candles/latest?symbol=BTCUSDT&interval=1m&limit=2"
    assert _times(client.get(url + "&offset=0")) == TIMES[-2:]
    assert _times(client.get(url + "&offset=2")) == TIMES[-4:-2]
    assert _times(client.get(url + "&offset=4&sort=desc")) == list(reversed(TIMES[:2]))
    assert _times(client.get(url + "&offset=6")) == []


def test_latest_empty_database(client):
    assert _times(client.get("/candles/latest?symbol=BTCUSDT&interval=1m")) == []


@pytest.mark.parametrize("query", ["sort=invalid", "offset=-1", "limit=0", "limit=501"])
def test_latest_rejects_invalid_parameters(client, query):
    response = client.get("/candles/latest?symbol=BTCUSDT&interval=1m&" + query)
    assert response.status_code == 422
