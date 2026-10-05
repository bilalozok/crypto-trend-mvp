import pytest
from sqlalchemy import func, select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.session import SessionLocal
from app.services.binance_backfill import collect_history
from app.services.binance_market import BinanceMarketError
from app.workers.history_backfill import BAR, DEFAULT_SYMBOLS, plan
from tests.test_formation_scan import seed


def payload(index):
    return [index * BAR, "100", "101", "99", "100", "2", (index + 1) * BAR - 1]


def test_paginated_backfill_is_idempotent_and_preserves_live_health(monkeypatch):
    calls = []

    def get(path, params):
        calls.append(params)
        assert path == "/api/v3/klines"
        assert params["endTime"] == 4 * BAR - 1
        assert params["interval"] == "15m"
        assert params["limit"] == 1000
        cursor = params["startTime"] // BAR
        return [payload(i) for i in range(cursor, min(cursor + 2, 4))]

    monkeypatch.setattr("app.services.binance_backfill._get", get)
    with SessionLocal() as db:
        seed(db, "BTCUSDT", count=0)
        symbol = db.get(BinanceSpotSymbol, "BTCUSDT")
        symbol.last_success_ms = 999
        symbol.last_attempt_ms = 998
        symbol.last_error = "ExistingError"
        db.commit()
        # A newer candle must remain outside the frozen historical range.
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                interval="15m",
                open_time=5 * BAR,
                open=50,
                high=51,
                low=49,
                close=50,
                volume=1,
            )
        )
        db.commit()
        for _ in range(2):
            result = collect_history(db, "BTCUSDT", 0, 4 * BAR, 5 * BAR, pause=0)
            assert result["status"] == "complete"
            assert result["stored_candles"] == 4
            assert result["missing_candles"] == 0
        assert len(calls) == 4
        assert db.scalar(select(func.count()).select_from(BinanceSpotCandle)) == 5
        db.expire_all()
        symbol = db.get(BinanceSpotSymbol, "BTCUSDT")
        assert (symbol.last_success_ms, symbol.last_attempt_ms, symbol.last_error) == (
            999,
            998,
            "ExistingError",
        )


@pytest.mark.parametrize("kind", ["unclosed", "duplicate", "bad_price"])
def test_invalid_page_writes_nothing(monkeypatch, kind):
    data = [payload(0), payload(1)]
    if kind == "unclosed":
        data.append(payload(2))
    elif kind == "duplicate":
        data.append(payload(1))
    else:
        data[-1][2] = "nan"
    monkeypatch.setattr("app.services.binance_backfill._get", lambda *args: data)
    with SessionLocal() as db:
        seed(db, "BTCUSDT", count=0)
        with pytest.raises(BinanceMarketError):
            collect_history(db, "BTCUSDT", 0, 2 * BAR, 2 * BAR, pause=0)
        assert db.scalar(select(func.count()).select_from(BinanceSpotCandle)) == 0


def test_gaps_and_short_listing_history_are_reported(monkeypatch):
    monkeypatch.setattr(
        "app.services.binance_backfill._get", lambda *args: [payload(1), payload(3)]
    )
    with SessionLocal() as db:
        seed(db, "BTCUSDT", count=0)
        result = collect_history(db, "BTCUSDT", 0, 4 * BAR, 4 * BAR, pause=0)
        assert result["status"] == "incomplete"
        assert result["missing_candles"] == 2


def test_future_range_and_stablecoin_rejected_before_request(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Network request must not occur")

    monkeypatch.setattr("app.services.binance_backfill._get", forbidden)
    with SessionLocal() as db:
        seed(db, "USD1USDT", base="USD1", count=0)
        with pytest.raises(ValueError):
            collect_history(db, "USD1USDT", 0, 2 * BAR, 2 * BAR)
        with pytest.raises(ValueError):
            collect_history(db, "USD1USDT", 0, 3 * BAR, 2 * BAR)


def test_plan_has_fixed_cutoff_and_extra_warmup():
    stamp = 2000000000000
    symbols, start, evaluation, end = plan(DEFAULT_SYMBOLS, 30, None, stamp)
    assert len(symbols) == 20
    assert "USD1USDT" not in symbols
    assert end == stamp // BAR * BAR
    assert end - evaluation == 2880 * BAR
    assert evaluation - start == 200 * BAR
    with pytest.raises(ValueError):
        plan("BTCUSDT", 30, "2026-10-05T01:00:00", stamp)
