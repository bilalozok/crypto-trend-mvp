from datetime import UTC, datetime
from math import isfinite
from statistics import fmean

from sqlalchemy.orm import Session

from app.db.models.candle import Candle
from app.schemas.signal import TrendOut
from app.utils.timeframes import INTERVAL_MS


def get_trend(
    db: Session,
    symbol: str,
    interval: str,
    short_period: int,
    long_period: int,
    now_ms: int,
) -> TrendOut:
    duration = INTERVAL_MS[interval]
    rows = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.interval == interval,
            Candle.open_time <= now_ms - duration,
        )
        .order_by(Candle.open_time.desc())
        .limit(long_period)
        .all()
    )
    rows.reverse()
    result = TrendOut(
        symbol=symbol,
        interval=interval,
        status="insufficient_data",
        short_period=short_period,
        long_period=long_period,
        candles_used=len(rows),
        candles_required=long_period,
        as_of=datetime.fromtimestamp(now_ms / 1000, tz=UTC),
    )
    if not rows:
        return result

    last = rows[-1]
    result.last_candle_open_time = datetime.fromtimestamp(last.open_time / 1000, tz=UTC)
    result.last_candle_close_time = datetime.fromtimestamp(
        (last.open_time + duration) / 1000, tz=UTC
    )
    result.stale = now_ms - (last.open_time + duration) >= duration
    if any(not isfinite(row.close) or row.close <= 0 for row in rows):
        result.status = "invalid_data"
        return result
    result.last_close = last.close
    if len(rows) < long_period:
        return result
    if any(
        right.open_time - left.open_time != duration
        for left, right in zip(rows, rows[1:], strict=False)
    ):
        result.status = "missing_data"
        return result

    closes = [row.close for row in rows]
    result.short_sma = fmean(closes[-short_period:])
    result.long_sma = fmean(closes)
    if result.short_sma > result.long_sma:
        result.trend = "up"
    elif result.short_sma < result.long_sma:
        result.trend = "down"
    else:
        result.trend = "flat"
    result.status = "ready"
    return result
