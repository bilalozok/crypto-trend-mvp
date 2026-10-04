from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TrendOut(BaseModel):
    symbol: str
    interval: str
    method: Literal["sma"] = "sma"
    status: Literal["ready", "insufficient_data", "missing_data", "invalid_data"]
    trend: Literal["up", "down", "flat"] | None = None
    short_period: int
    long_period: int
    candles_used: int
    candles_required: int
    short_sma: float | None = None
    long_sma: float | None = None
    last_close: float | None = None
    last_candle_open_time: datetime | None = None
    last_candle_close_time: datetime | None = None
    stale: bool | None = None
    as_of: datetime
