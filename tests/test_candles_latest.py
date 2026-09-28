from app.db.session import SessionLocal
from app.db.models.candle import Candle


def reset_candles():
    db = SessionLocal()
    try:
        db.query(Candle).delete()
        db.commit()
    finally:
        db.close()


def seed_candles():
    db = SessionLocal()
    try:
        rows = [
            Candle(
                symbol="BTCUSDT",
                interval="1h",
                open_time=1000,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=10.0,
            ),
            Candle(
                symbol="BTCUSDT",
                interval="1h",
                open_time=2000,
                open=1.5,
                high=2.5,
                low=1.0,
                close=2.0,
                volume=20.0,
            ),
            Candle(
                symbol="ETHUSDT",
                interval="1h",
                open_time=1500,
                open=10.0,
                high=12.0,
                low=9.0,
                close=11.0,
                volume=5.0,
            ),
        ]
        db.add_all(rows)
        db.commit()
    finally:
        db.close()


def test_latest_filters_and_order(client):
    reset_candles()
    seed_candles()

    r = client.get("/candles/latest?symbol=BTCUSDT&interval=1h&limit=5")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 2

    # En yeni önce bekliyoruz
    assert data[0]["open_time"] == 2000
    assert data[1]["open_time"] == 1000
    assert all(x["symbol"] == "BTCUSDT" for x in data)
