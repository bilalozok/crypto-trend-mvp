import requests


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

    assert "created" in d2
    assert "skipped_existing" in d2
    assert d2["created"] == 0


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
