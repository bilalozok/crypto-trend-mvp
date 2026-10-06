"""Per-bar geometry on independent higher timeframes; preserve the 15m engine."""

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select

from app.db.models.portfolio_timeframe import PortfolioCandle
from app.services import formations

INTERVALS = {"4h": 14_400_000, "1d": 86_400_000}


def analyze_rows(rows, symbol, stamp, interval):
    bar = INTERVALS[interval]
    # The geometric engine operates in bar indices. Give each real source bar
    # one synthetic 15m index, then restore every datetime to the source clock.
    # OHLCV is never resampled, and no process-global BAR is changed.
    invalid_source = any(row.open_time % bar or row.open_time + bar > stamp for row in rows)
    if invalid_source:
        result = formations.analyze_rows([], symbol, stamp // bar * formations.BAR)
        result.update(
            status="invalid_data",
            interval=interval,
            as_of=formations.timestamp(stamp),
            method_version="portfolio_bar_geometry_v1",
        )
        return result
    normalized = [
        SimpleNamespace(
            open_time=row.open_time // bar * formations.BAR,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
        )
        for row in rows
    ]
    result = formations.analyze_rows(normalized, symbol, stamp // bar * formations.BAR)
    # Reject source misalignment and open candles before running detection.
    if any(row.open_time % bar or row.open_time + bar > stamp for row in rows):
        result.update(status="invalid_data", patterns=[])

    def restore(item):
        if isinstance(item, datetime):
            normalized_ms = round(item.timestamp() * 1000)
            return formations.timestamp(round(normalized_ms * bar / formations.BAR))
        if isinstance(item, dict):
            return {key: restore(value) for key, value in item.items()}
        if isinstance(item, list):
            return [restore(value) for value in item]
        return item

    result["patterns"] = restore(result["patterns"])
    result.update(
        interval=interval,
        as_of=formations.timestamp(stamp),
        method_version="portfolio_bar_geometry_v1",
    )
    return result


def analyze(db, symbol, stamp, interval):
    bar = INTERVALS[interval]
    rows = list(
        reversed(
            db.scalars(
                select(PortfolioCandle)
                .where(
                    PortfolioCandle.symbol == symbol,
                    PortfolioCandle.interval == interval,
                    PortfolioCandle.open_time + bar <= stamp,
                )
                .order_by(PortfolioCandle.open_time.desc())
                .limit(200)
            ).all()
        )
    )
    result = analyze_rows(rows, symbol, stamp, interval)
    result["candle_close_time"] = formations.timestamp(rows[-1].open_time + bar) if rows else None
    return result
