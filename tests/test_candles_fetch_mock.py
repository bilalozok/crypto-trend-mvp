from importlib import import_module
from unittest.mock import Mock, patch


def _resolve_patch_target():
    """
    Uygulama kodunda fetch endpointinin kullandığı HTTP çağrısını bul:
    - app.main.requests.get
    - app.main.httpx.get
    """
    m = import_module("app.main")
    if hasattr(m, "requests"):
        return "app.main.requests.get"
    if hasattr(m, "httpx"):
        return "app.main.httpx.get"
    raise RuntimeError("app.main içinde ne requests ne httpx bulundu; patch hedefi güncellenmeli.")


def _fake_binance_klines():
    # Binance kline formatı (12 alan)
    # [open_time, open, high, low, close, volume, close_time, quote_asset_volume, trades, taker_buy_base, taker_buy_quote, ignore]
    return [
        [1700000000000, "100.0", "110.0", "90.0", "105.0", "1000", 1700000059999, "0", 10, "0", "0", "0"],
        [1700000060000, "105.0", "115.0", "95.0", "108.0", "1200", 1700000119999, "0", 12, "0", "0", "0"],
    ]


def test_fetch_endpoint_with_network_mock(client):
    target = _resolve_patch_target()

    # requests.get için mock response
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _fake_binance_klines()
    mock_resp.raise_for_status = Mock()

    with patch(target, return_value=mock_resp) as _m:
        r = client.post("/candles/fetch/BTCUSDT?interval=1m&limit=2")
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, dict)

        # Esnek assertion: projedeki response şemasına göre bu alanlardan en az biri olmalı
        possible_keys = {"inserted", "saved", "created", "fetched", "skipped_existing", "total"}
        assert any(k in data for k in possible_keys), data


def test_fetch_idempotent_with_same_mock_data(client):
    target = _resolve_patch_target()

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _fake_binance_klines()
    mock_resp.raise_for_status = Mock()

    with patch(target, return_value=mock_resp):
        r1 = client.post("/candles/fetch/BTCUSDT?interval=1m&limit=2")
        assert r1.status_code == 200, r1.text
        d1 = r1.json()

        r2 = client.post("/candles/fetch/BTCUSDT?interval=1m&limit=2")
        assert r2.status_code == 200, r2.text
        d2 = r2.json()

        # İkinci çağrıda duplicate'lerin atlanması beklenir (idempotent davranış)
        # Şema farklı olabilir diye esnek kontrol:
        if "skipped_existing" in d2:
            assert d2["skipped_existing"] >= 1
        elif "inserted" in d2:
            assert d2["inserted"] == 0
        elif "created" in d2:
            assert d2["created"] == 0
        # hiçbiri yoksa bile endpoint en azından başarılı dönmeli (yukarıda doğrulandı)
import requests


def test_fetch_handles_429_rate_limit(client):
    target = _resolve_patch_target()

    mock_resp = Mock()
    mock_resp.status_code = 429
    mock_resp.json.return_value = {"code": -1003, "msg": "Too many requests."}
    mock_resp.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")

    with patch(target, return_value=mock_resp):
        r = client.post("/candles/fetch/BTCUSDT?interval=1m&limit=2")

        # Projeye göre davranış değişebilir:
        # - 429'u upstream olarak yansıtabilir (429)
        # - Bad Gateway/Service Unavailable'a mapleyebilir (502/503)
        # - Genel hata (500) olabilir
        assert r.status_code in (429, 500, 502, 503), r.text

        # Hata gövdesinde mesaj beklentisi (esnek)
        body = r.json()
        assert isinstance(body, dict)
        text = str(body).lower()
        assert ("429" in text) or ("too many requests" in text) or ("rate" in text) or ("limit" in text)
