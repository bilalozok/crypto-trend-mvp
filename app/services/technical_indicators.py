"""Descriptive closed-candle indicators. No candidate score or trade rules."""

import math
from statistics import pstdev

from sqlalchemy import select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.portfolio_timeframe import PortfolioCandle
from app.services.fibonacci_context import context
from app.services.formations import timestamp

INTERVALS = {"15m": 900_000, "4h": 14_400_000, "1d": 86_400_000}
VERSION = "closed_indicators_v1"


def ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    value = sum(values[:period]) / period
    out[period - 1] = value
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        value += alpha * (values[i] - value)
        out[i] = value
    return out


def rsi(values, period=14):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    changes = [b - a for a, b in zip(values, values[1:], strict=False)]
    gain = sum(max(0, d) for d in changes[:period]) / period
    loss = sum(max(0, -d) for d in changes[:period]) / period
    for i in range(period, len(values)):
        if i > period:
            d = changes[i - 1]
            gain = (gain * (period - 1) + max(0, d)) / period
            loss = (loss * (period - 1) + max(0, -d)) / period
        if gain == loss == 0:
            out[i] = 50.0
        elif loss == 0:
            out[i] = 100.0
        else:
            out[i] = 100 - 100 / (1 + gain / loss)
    return out


def calculate(values):
    fast, slow = ema(values, 12), ema(values, 26)
    macd = [a - b if b is not None else None for a, b in zip(fast, slow, strict=True)]
    signal = [None] * min(25, len(values)) + ema(macd[25:], 9)
    momentum, trend = rsi(values), ema(values, 50)
    series = []
    for i, close in enumerate(values):
        sma = sum(values[i - 49 : i + 1]) / 50 if i >= 49 else None
        middle = sum(values[i - 19 : i + 1]) / 20 if i >= 19 else None
        deviation = pstdev(values[i - 19 : i + 1]) if i >= 19 else None
        upper = middle + 2 * deviation if middle is not None else None
        lower = middle - 2 * deviation if middle is not None else None
        histogram = macd[i] - signal[i] if signal[i] is not None else None
        series.append(
            dict(
                close=close,
                rsi=momentum[i],
                sma50=sma,
                ema50=trend[i],
                macd=macd[i],
                signal=signal[i],
                histogram=histogram,
                bb_middle=middle,
                bb_upper=upper,
                bb_lower=lower,
                bb_width_pct=(upper - lower) / middle * 100 if middle else None,
            )
        )
    return series


def analyze_rows(rows, interval, stamp):
    bar = INTERVALS[interval]
    result = dict(interval=interval, status="insufficient_data", series=[], latest=None)
    if len(rows) < 200:
        return result
    if any(
        row.open_time % bar
        or row.open_time + bar > stamp
        or not math.isfinite(row.close)
        or row.close <= 0
        for row in rows
    ) or any(b.open_time - a.open_time != bar for a, b in zip(rows, rows[1:], strict=False)):
        result["status"] = "invalid_data"
        return result
    if rows[-1].open_time + bar != stamp // bar * bar:
        result["status"] = "stale_data"
        return result
    series = calculate([row.close for row in rows])
    for row, point in zip(rows, series, strict=True):
        point["at"] = timestamp(row.open_time + bar)
    result.update(
        status="ready", series=series[-100:], latest=series[-1], fibonacci=context(rows, bar)
    )
    return result


def report(db, symbol, stamp):
    item = db.get(BinanceSpotSymbol, symbol)
    if item is None or not item.active:
        return None
    horizons = []
    for interval, bar in INTERVALS.items():
        model = BinanceSpotCandle if interval == "15m" else PortfolioCandle
        rows = list(
            reversed(
                db.scalars(
                    select(model)
                    .where(
                        model.symbol == symbol,
                        model.interval == interval,
                        model.open_time + bar <= stamp,
                    )
                    .order_by(model.open_time.desc())
                    .limit(200)
                ).all()
            )
        )
        horizons.append(analyze_rows(rows, interval, stamp))
    return dict(symbol=symbol, checked_at=timestamp(stamp), version=VERSION, horizons=horizons)
