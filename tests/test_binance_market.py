from datetime import datetime

import pytest
import requests

from app.db.models.candle import Candle
from app.db.session import SessionLocal
from app.services import binance_market as market

NOW = 1_700_002_800_000


def _symbol(symbol, base, **updates):
    row = {
        "symbol": symbol,
        "baseAsset": base,
        "quoteAsset": "USDT",
        "status": "TRADING",
        "isSpotTradingAllowed": True,
    }
    row.update(updates)
    return row


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.payload


@pytest.fixture
def binance_mock(monkeypatch):
    instance = market.SpotCatalog()
    monkeypatch.setattr(market, "catalog", instance)
    monkeypatch.setattr("app.main.catalog", instance)
    payloads = {
        "/api/v3/exchangeInfo": {
            "symbols": [
                _symbol("BTCUSDT", "BTC"),
                _symbol("ETHUSDT", "ETH"),
                _symbol("BTCEUR", "BTC", quoteAsset="EUR"),
                _symbol("OLDUSDT", "OLD", status="BREAK"),
                _symbol("FUTUSDT", "FUT", isSpotTradingAllowed=False),
            ]
        },
        "/api/v3/ticker/24hr": [
            {"symbol": "BTCUSDT", "quoteVolume": "1000"},
            {"symbol": "ETHUSDT", "quoteVolume": "2000"},
        ],
        "/api/v3/klines": [
            [NOW - 900_000, "100", "110", "90", "105", "1000", NOW - 1],
            [NOW, "100", "110", "90", "108", "1000", NOW + 900_000 - 1],
        ],
    }
    calls = []

    def get(url, params=None, timeout=None):
        assert url.startswith(market.BASE_URL)
        path = url.removeprefix(market.BASE_URL)
        calls.append((path, params))
        payload = payloads[path]
        return payload if isinstance(payload, Response) else Response(payload)

    monkeypatch.setattr(requests, "get", get)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(NOW / 1000, tz=tz)

    monkeypatch.setattr(market, "datetime", FixedClock)
    return payloads, calls


def test_catalog_filters_spot_usdt_and_sorts_volume(client, binance_mock):
    response = client.get("/market/binance/symbols")
    assert response.status_code == 200
    body = response.json()
    assert body["exchange"] == "binance"
    assert body["market"] == "spot"
    assert body["total"] == 2
    assert [s["symbol"] for s in body["symbols"]] == ["ETHUSDT", "BTCUSDT"]
    assert body["symbols"][0]["quote_volume_24h"] == 2000


def test_catalog_pagination_and_volume_filter(client, binance_mock):
    body = client.get("/market/binance/symbols?limit=1&offset=1").json()
    assert body["total"] == 2
    assert [row["symbol"] for row in body["symbols"]] == ["BTCUSDT"]
    body = client.get("/market/binance/symbols?min_quote_volume=1500").json()
    assert body["total"] == 1
    assert body["symbols"][0]["symbol"] == "ETHUSDT"


def test_catalog_caches_snapshot_for_five_minutes(client, binance_mock, monkeypatch):
    _, calls = binance_mock
    monkeypatch.setattr(market, "monotonic", lambda: 0)
    first = client.get("/market/binance/symbols").json()
    monkeypatch.setattr(market, "monotonic", lambda: 299)
    assert client.get("/market/binance/symbols").json()["as_of"] == first["as_of"]
    assert len(calls) == 2
    monkeypatch.setattr(market, "monotonic", lambda: 300)
    assert client.get("/market/binance/symbols").status_code == 200
    assert len(calls) == 4


def test_preview_uses_binance_closed_15m_candles_without_writes(client, binance_mock):
    _, calls = binance_mock
    response = client.get("/market/binance/candles/preview?symbol=btcusdt&limit=25")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stored"] is False
    assert len(body["candles"]) == 1
    assert body["candles"][0]["close"] == 105
    assert body["candles"][0]["open_time"] == "2023-11-14T22:45:00Z"
    assert calls[-1] == ("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "15m", "limit": 25})
    with SessionLocal() as db:
        assert db.query(Candle).count() == 0


def test_preview_rejects_non_spot_symbol(client, binance_mock):
    response = client.get("/market/binance/candles/preview?symbol=FUTUSDT")
    assert response.status_code == 404
    assert len(binance_mock[1]) == 2


@pytest.mark.parametrize("status", [403, 451, 418, 429])
def test_provider_denial_has_no_okx_fallback(client, binance_mock, status):
    payloads, calls = binance_mock
    payloads["/api/v3/exchangeInfo"] = Response({}, status)
    response = client.get("/market/binance/symbols")
    assert response.status_code == 503
    assert len(calls) == 1


@pytest.mark.parametrize("volume", ["nan", "inf", "-1", "invalid"])
def test_invalid_volume_rejects_snapshot(client, binance_mock, volume):
    payloads, _ = binance_mock
    payloads["/api/v3/ticker/24hr"][0]["quoteVolume"] = volume
    assert client.get("/market/binance/symbols").status_code == 502


def test_partial_volume_snapshot_is_not_cached(client, binance_mock):
    payloads, calls = binance_mock
    payloads["/api/v3/ticker/24hr"] = []
    assert client.get("/market/binance/symbols").status_code == 502
    assert client.get("/market/binance/symbols").status_code == 502
    assert len(calls) == 4


def test_preview_invalid_duration_returns_error(client, binance_mock):
    payloads, _ = binance_mock
    payloads["/api/v3/klines"][0][6] -= 1
    assert client.get("/market/binance/candles/preview?symbol=BTCUSDT").status_code == 502


@pytest.mark.parametrize(
    "url",
    [
        "/market/binance/symbols?limit=0",
        "/market/binance/symbols?offset=-1",
        "/market/binance/symbols?min_quote_volume=nan",
        "/market/binance/candles/preview?symbol=BTCUSDT&limit=101",
    ],
)
def test_market_parameters_are_validated(client, url):
    assert client.get(url).status_code == 422
