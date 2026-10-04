from typing import Any

import requests

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def fetch_klines(symbol: str, interval: str = "1h", limit: int = 200) -> list[list[Any]]:
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit,
    }

    try:
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Binance request failed: {e}") from e

    data = resp.json()

    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected Binance response: {data}")

    return data
