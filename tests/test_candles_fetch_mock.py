import requests
from sqlalchemy import select

from app.db.models.candle import Candle
from app.db.session import SessionLocal


def _fake_binance_klines():
    # Binance kline formatı (12 alan):
    # [open_time, open, high, low, close, volume, close_time,
    #  quote_asset_volume, trades, taker_buy_base, taker_buy_quote, ignore]
    return [
        [
            1700000000000,
            "100.0",
            "110.0",
            "90.0",
            "105.0",
            "1000",
            1700000059999,
            "0",
            10,
            "0",
            "0",
            "0",
        ],
        [
            1700000060000,
            "105.0",
            "115.0",
            "95.0",
            "108.0",
            "1200",
            1700000119999,
            "0",
            12,
            "0",
            "0",
            "0",
        ],
    ]


class _RespOK:
    def raise_for_status(self):
        return None

    def json(self):
        return _fake_binance_klines()


class _Resp429:
    status_code = 429

    def raise_for_status(self):
        raise requests.HTTPError("429 Too Many Requests")

    def json(self):
        return {"code": -1003, "msg": "Too many requests"}


def test_fetch_idempotent_with_mock(client, monkeypatch):
    def fake_get(*args, **kwargs):
        return _RespOK()

    monkeypatch.setattr(requests, "get", fake_get)

    r1 = client.post("/candles/fetch/BTCUSDT?interval=1m&limit=2")
    assert r1.status_code == 200, r1.text

    r2 = client.post("/candles/fetch/BTCUSDT?interval=1m&limit=2")
    assert r2.status_code == 200, r2.text
    d2 = r2.json()

    assert isinstance(d2, list)
    assert len(d2) == 2
    assert d2 == r1.json()
    assert d2[0]["open_time"] == "2023-11-14T22:13:20Z"
    with SessionLocal() as db:
        rows = db.scalars(select(Candle)).all()
        assert len(rows) == 2
        assert [row.open_time for row in rows] == [1700000000000, 1700000060000]

    latest = client.get("/candles/latest?symbol=BTCUSDT&interval=1m&limit=2")
    assert latest.status_code == 200
    assert latest.json() == d2


def test_fetch_429_returns_502(client, monkeypatch):
    def fake_get(*args, **kwargs):
        return _Resp429()

    monkeypatch.setattr(requests, "get", fake_get)

    r = client.post("/candles/fetch/BTCUSDT?interval=1m&limit=2")
    assert r.status_code == 502

    body = r.json()
    assert isinstance(body, dict)
    text = str(body).lower()
    assert ("429" in text) or ("too many requests" in text) or ("rate" in text) or ("limit" in text)
