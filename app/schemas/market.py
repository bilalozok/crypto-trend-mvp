from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.candle import CandleOut


class SpotSymbolOut(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: Literal["USDT"] = "USDT"
    quote_volume_24h: float


class SpotCatalogOut(BaseModel):
    exchange: Literal["binance"] = "binance"
    market: Literal["spot"] = "spot"
    quote_asset: Literal["USDT"] = "USDT"
    as_of: datetime
    min_quote_volume: float
    total: int
    limit: int
    offset: int
    symbols: list[SpotSymbolOut]


class BinancePreviewOut(BaseModel):
    exchange: Literal["binance"] = "binance"
    market: Literal["spot"] = "spot"
    interval: Literal["15m"] = "15m"
    symbol: str
    as_of: datetime
    stored: Literal[False] = False
    candles: list[CandleOut]
