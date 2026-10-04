from datetime import UTC, datetime

import pytest

from app.db.models.candle import Candle
from app.db.session import SessionLocal
from app.services.signal_engine import get_trend

HOUR = 3_600_000
NOW = 1_700_002_800_000


def _seed(closes, *, symbol="BTCUSDT", interval="1h", start=None):
    start = NOW - len(closes) * HOUR if start is None else start
    with SessionLocal() as db:
        db.add_all(
            Candle(
                symbol=symbol,
                interval=interval,
                open_time=start + i * HOUR,
                open=100,
                high=110,
                low=90,
                close=close,
                volume=1000,
            )
            for i, close in enumerate(closes)
        )
        db.commit()


def _summary(short=2, long=5):
    with SessionLocal() as db:
        return get_trend(db, "BTCUSDT", "1h", short, long, NOW)


@pytest.mark.parametrize(
    "closes,trend,short_sma,long_sma",
    [
        ([1, 2, 3, 4, 5], "up", 4.5, 3),
        ([5, 4, 3, 2, 1], "down", 1.5, 3),
        ([3, 3, 3, 3, 3], "flat", 3, 3),
    ],
)
def test_trend_compares_smas(closes, trend, short_sma, long_sma):
    _seed(closes)
    result = _summary()
    assert result.status == "ready"
    assert result.trend == trend
    assert result.short_sma == short_sma
    assert result.long_sma == long_sma
    assert result.candles_used == 5
    assert result.stale is False
    assert result.last_candle_close_time == datetime.fromtimestamp(NOW / 1000, tz=UTC)


def test_trend_uses_only_latest_closed_candles():
    _seed([50, 1, 2, 3, 4, 5])
    _seed([999, 999], start=NOW)
    result = _summary()
    assert result.status == "ready"
    assert result.long_sma == 3
    assert result.short_sma == 4.5
    assert result.last_close == 5


def test_trend_filters_symbol_and_interval():
    _seed([1, 2, 3, 4, 5])
    _seed([999] * 5, symbol="ETHUSDT")
    _seed([999] * 5, interval="1m")
    assert _summary().long_sma == 3


def test_trend_reports_empty_data():
    result = _summary()
    assert result.status == "insufficient_data"
    assert result.candles_used == 0
    assert result.candles_required == 5
    assert result.trend is None
    assert result.stale is None


def test_trend_reports_insufficient_data():
    _seed([1, 2, 3, 4])
    result = _summary()
    assert result.status == "insufficient_data"
    assert result.candles_used == 4
    assert result.short_sma is None
    assert result.long_sma is None


def test_trend_reports_gap_in_window():
    _seed([1, 2, 3, 4, 5, 6])
    with SessionLocal() as db:
        db.query(Candle).filter(Candle.open_time == NOW - 3 * HOUR).delete()
        db.commit()
    result = _summary()
    assert result.status == "missing_data"
    assert result.trend is None


def test_trend_marks_stale_data():
    _seed([1, 2, 3, 4, 5], start=NOW - 6 * HOUR)
    result = _summary()
    assert result.status == "ready"
    assert result.stale is True


@pytest.mark.parametrize("close", [-1, 0, float("inf")])
def test_trend_rejects_invalid_closes(close):
    _seed([1, 2, 3, 4, close])
    result = _summary()
    assert result.status == "invalid_data"
    assert result.trend is None
    assert result.last_close is None


def test_trend_http_response(client, monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(NOW / 1000, tz=tz)

    monkeypatch.setattr("app.main.datetime", FixedClock)
    _seed([1, 2, 3, 4, 5])
    response = client.get("/signals/trend?symbol=btcusdt&interval=1h&short_period=2&long_period=5")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["method"] == "sma"
    assert body["status"] == "ready"
    assert body["trend"] == "up"
    assert body["last_candle_close_time"] == "2023-11-14T23:00:00Z"
    with SessionLocal() as db:
        assert db.query(Candle).count() == 5


def test_trend_http_empty(client):
    response = client.get("/signals/trend?symbol=BTCUSDT")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert body["candles_required"] == 20


@pytest.mark.parametrize(
    "query",
    [
        "short_period=20&long_period=20",
        "short_period=21&long_period=20",
        "short_period=0",
        "long_period=501",
        "long_period=1",
        "interval=1M",
        "symbol=BTC/USDT",
    ],
)
def test_trend_http_validation(client, query):
    response = client.get("/signals/trend?symbol=BTCUSDT&" + query)
    assert response.status_code == 422
