from types import SimpleNamespace

import pytest

from app.services.technical_indicators import INTERVALS, analyze_rows, calculate, ema, rsi


def test_known_wilder_rsi_and_seeded_ema():
    prices = [
        44.34,
        44.09,
        44.15,
        43.61,
        44.33,
        44.83,
        45.1,
        45.42,
        45.84,
        46.08,
        45.89,
        46.03,
        45.61,
        46.28,
        46.28,
        46.0,
    ]
    assert rsi(prices)[14] == pytest.approx(70.464135, abs=0.00001)
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]
    assert rsi([5] * 200)[-1] == 50
    assert rsi(list(range(1, 201)))[-1] == 100
    assert rsi(list(range(200, 0, -1)))[-1] == 0


def test_indicator_identities_and_no_future_dependency():
    values = [100 + i / 10 + (i % 7) for i in range(200)]
    result = calculate(values)
    p = result[-1]
    assert p["sma50"] == pytest.approx(sum(values[-50:]) / 50)
    assert p["bb_middle"] == pytest.approx(sum(values[-20:]) / 20)
    assert p["bb_upper"] + p["bb_lower"] == pytest.approx(2 * p["bb_middle"])
    assert p["histogram"] == pytest.approx(p["macd"] - p["signal"])
    assert calculate(values + [500])[:-1] == result
    flat = calculate([100] * 200)[-1]
    assert flat["macd"] == flat["signal"] == flat["histogram"] == 0
    assert flat["bb_upper"] == flat["bb_lower"] == 100
    assert len(calculate([1, 2])) == 2


@pytest.mark.parametrize("interval", INTERVALS)
def test_closed_contiguous_fresh_data_required(interval):
    bar = INTERVALS[interval]
    rows = [SimpleNamespace(open_time=i * bar, close=100 + i) for i in range(200)]
    assert analyze_rows(rows, interval, 200 * bar)["status"] == "ready"
    assert len(analyze_rows(rows, interval, 200 * bar)["series"]) == 100
    assert analyze_rows(rows, interval, 201 * bar)["status"] == "stale_data"
    assert analyze_rows(rows, interval, 199 * bar)["status"] == "invalid_data"
    assert analyze_rows(rows[:-1], interval, 200 * bar)["status"] == "insufficient_data"
    rows[50].open_time += 1
    assert analyze_rows(rows, interval, 200 * bar)["latest"] is None
    rows[50].open_time -= 1
    rows[50].close = float("nan")
    assert analyze_rows(rows, interval, 200 * bar)["status"] == "invalid_data"


def test_indicator_api_uses_closed_stored_data(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle
    from app.db.session import SessionLocal
    from tests.test_formation_scan import seed

    bar = INTERVALS["15m"]
    monkeypatch.setattr("app.main.now_ms", lambda: 200 * bar)
    with SessionLocal() as db:
        seed(db, "BTCUSDT")
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                open_time=200 * bar,
                interval="15m",
                open=999,
                high=999,
                low=999,
                close=999,
                volume=1,
            )
        )
        db.commit()
    response = client.get("/analysis/binance/indicators?symbol=btcusdt")
    assert response.status_code == 200
    data = response.json()
    assert data["horizons"][0]["status"] == "ready"
    assert data["horizons"][0]["latest"]["close"] != 999
    assert data["horizons"][1]["status"] == "insufficient_data"
    assert data["horizons"][2]["status"] == "insufficient_data"
    assert client.get("/analysis/binance/indicators?symbol=MISSINGUSDT").status_code == 404
