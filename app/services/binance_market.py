"""Read-only Binance Spot market data. No cross-exchange fallback."""

from datetime import UTC, datetime
from math import isfinite
from threading import Lock
from time import monotonic

import requests

from app.schemas.candle import CandleOut
from app.schemas.market import BinancePreviewOut, SpotSymbolOut

BASE_URL = "https://data-api.binance.vision"
CATALOG_TTL = 300


class BinanceMarketError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _get(path: str, params: dict | None = None):
    try:
        response = requests.get(BASE_URL + path, params=params, timeout=(15, 20))
        if response.status_code in (418, 429):
            raise BinanceMarketError("Binance rate limit reached; retry later", 503)
        if response.status_code in (403, 451):
            raise BinanceMarketError("Binance market data is unavailable from this server", 503)
        response.raise_for_status()
        return response.json()
    except BinanceMarketError:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise BinanceMarketError("Binance market data request failed") from exc


def _active_symbols(payload) -> dict[str, str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise BinanceMarketError("Invalid Binance exchange information")
    result = {}
    for item in payload["symbols"]:
        if not isinstance(item, dict):
            raise BinanceMarketError("Invalid Binance symbol")
        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed") is True
        ):
            symbol, base = item.get("symbol"), item.get("baseAsset")
            if not isinstance(symbol, str) or not isinstance(base, str) or not base:
                raise BinanceMarketError("Invalid Binance symbol")
            result[symbol] = base
    if not result:
        raise BinanceMarketError("Binance returned no active Spot USDT symbols")
    return result


class SpotCatalog:
    def __init__(self):
        self.lock = Lock()
        self.cached = None
        self.expires_at = 0.0

    def snapshot(self) -> tuple[datetime, tuple[SpotSymbolOut, ...]]:
        with self.lock:
            if self.cached is not None and monotonic() < self.expires_at:
                return self.cached
            symbols = _active_symbols(_get("/api/v3/exchangeInfo"))
            tickers = _get("/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                raise BinanceMarketError("Invalid Binance volume data")
            volumes = {}
            try:
                for item in tickers:
                    symbol = item["symbol"]
                    if symbol in symbols:
                        volume = float(item["quoteVolume"])
                        if not isfinite(volume) or volume < 0:
                            raise ValueError
                        volumes[symbol] = volume
            except (KeyError, TypeError, ValueError) as exc:
                raise BinanceMarketError("Invalid Binance volume data") from exc
            if set(volumes) != set(symbols):
                raise BinanceMarketError("Binance volume snapshot is incomplete; retry later")
            rows = tuple(
                SpotSymbolOut(symbol=s, base_asset=base, quote_volume_24h=volumes[s])
                for s, base in symbols.items()
            )
            rows = tuple(sorted(rows, key=lambda row: (-row.quote_volume_24h, row.symbol)))
            self.cached = (datetime.now(UTC), rows)
            self.expires_at = monotonic() + CATALOG_TTL
            return self.cached


catalog = SpotCatalog()


def preview_candles(symbol: str, limit: int) -> BinancePreviewOut:
    _, symbols = catalog.snapshot()
    symbol = symbol.upper()
    if symbol not in {row.symbol for row in symbols}:
        raise BinanceMarketError("Symbol is not an active Binance Spot USDT pair", 404)
    payload = _get("/api/v3/klines", {"symbol": symbol, "interval": "15m", "limit": limit})
    if not isinstance(payload, list) or not payload:
        raise BinanceMarketError("Binance returned no valid candles")
    now = datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    candles = []
    try:
        for item in payload:
            open_time, close_time = int(item[0]), int(item[6])
            # Require a real 15-minute Binance bar; never trust the local clock alone.
            if open_time < 0 or close_time != open_time + 900_000 - 1:
                raise ValueError
            prices = [float(item[i]) for i in range(1, 6)]
            if any(not isfinite(value) or value < 0 for value in prices):
                raise ValueError
            if any(value <= 0 for value in prices[:4]):
                raise ValueError
            if close_time >= now_ms:
                continue
            candles.append(
                CandleOut(
                    symbol=symbol,
                    interval="15m",
                    open_time=datetime.fromtimestamp(open_time / 1000, tz=UTC),
                    open=prices[0],
                    high=prices[1],
                    low=prices[2],
                    close=prices[3],
                    volume=prices[4],
                )
            )
    except (IndexError, TypeError, ValueError) as exc:
        raise BinanceMarketError("Binance returned invalid candle data") from exc
    candles.sort(key=lambda candle: candle.open_time)
    return BinancePreviewOut(symbol=symbol, as_of=now, candles=candles)
