from __future__ import annotations

from typing import Any

import requests

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"

_INTERVAL_MAP: dict[str, str] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "12h": "12H",
    "1d": "1D",
    "1w": "1W",
}


def _to_okx_inst_id(symbol: str) -> str:
    s = symbol.upper().strip()
    if s.endswith("USDT"):
        base = s[:-4]
        return f"{base}-USDT"
    if s.endswith("USD"):
        base = s[:-3]
        return f"{base}-USD"
    if len(s) > 4:
        return f"{s[:-4]}-{s[-4:]}"
    return s


def _binance_try(symbol: str, interval: str, limit: int) -> list[list[Any]]:
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected Binance response: {data}")
    return data


def _okx_try(symbol: str, interval: str, limit: int) -> list[list[Any]]:
    bar = _INTERVAL_MAP.get(interval)
    if not bar:
        raise RuntimeError(f"Unsupported interval for OKX fallback: {interval}")

    inst_id = _to_okx_inst_id(symbol)
    okx_limit = max(1, min(int(limit), 100))
    params = {"instId": inst_id, "bar": bar, "limit": okx_limit}

    resp = requests.get(OKX_CANDLES_URL, params=params, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    if str(payload.get("code")) != "0":
        raise RuntimeError(f"OKX error: {payload}")

    data = payload.get("data", [])
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected OKX response: {payload}")

    out: list[list[Any]] = []
    for row in reversed(data):
        ts_ms = int(row[0])
        open_price = row[1]
        high_price = row[2]
        low_price = row[3]
        close_price = row[4]
        volume = row[5]

        converted = [
            ts_ms,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            ts_ms,
            "0",
            0,
            "0",
            "0",
            "0",
        ]
        out.append(converted)

    return out


def fetch_klines(symbol: str, interval: str = "1h", limit: int = 200) -> list[list[Any]]:
    symbol = symbol.upper()
    try:
        return _binance_try(symbol=symbol, interval=interval, limit=limit)
    except Exception as binance_err:
        try:
            return _okx_try(symbol=symbol, interval=interval, limit=limit)
        except Exception as okx_err:
            raise RuntimeError(
                f"Binance failed: {binance_err}; OKX fallback failed: {okx_err}"
            ) from okx_err
