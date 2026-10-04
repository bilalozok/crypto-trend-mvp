from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CandleOut(BaseModel):
    symbol: str
    interval: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    model_config = ConfigDict(from_attributes=True)
