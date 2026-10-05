"""Bounded historical collection; does not modify live-worker health or events."""

import math
from time import sleep

from sqlalchemy import select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.services.binance_collection import BAR_MS, insert_for
from app.services.binance_coverage import STABLECOIN_BASES
from app.services.binance_market import BinanceMarketError, _get


def parse_page(payload, symbol, start, end):
    if not isinstance(payload, list) or len(payload) > 1000:
        raise BinanceMarketError("Invalid historical candle page")
    values = []
    previous = start - BAR_MS
    try:
        for row in payload:
            opened, closed = int(row[0]), int(row[6])
            o, h, low, c, volume = (float(row[i]) for i in range(1, 6))
            if (
                opened < start
                or opened >= end
                or opened <= previous
                or opened % BAR_MS
                or closed != opened + BAR_MS - 1
                or any(not math.isfinite(v) or v <= 0 for v in (o, h, low, c))
                or not math.isfinite(volume)
                or volume < 0
                or low > min(o, c)
                or h < max(o, c)
                or low > h
            ):
                raise ValueError
            values.append(
                dict(
                    symbol=symbol,
                    interval="15m",
                    open_time=opened,
                    open=o,
                    high=h,
                    low=low,
                    close=c,
                    volume=volume,
                )
            )
            previous = opened
    except (ValueError, TypeError, IndexError, OverflowError) as exc:
        raise BinanceMarketError("Invalid historical candle page") from exc
    return values


def save_page(db, values):
    # Small statements also stay below older SQLite parameter limits in tests.
    try:
        for offset in range(0, len(values), 100):
            statement = insert_for(db, BinanceSpotCandle).values(values[offset : offset + 100])
            statement = statement.on_conflict_do_update(
                index_elements=["symbol", "open_time"],
                set_={
                    key: getattr(statement.excluded, key)
                    for key in ("open", "high", "low", "close", "volume")
                },
            )
            db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise


def collect_history(db, symbol, start, end, stamp, pause=0.25, progress=None):
    if (
        start < 0
        or start % BAR_MS
        or end % BAR_MS
        or start >= end
        or end > stamp // BAR_MS * BAR_MS
        or end - start > 92 * 96 * BAR_MS
    ):
        raise ValueError("Invalid or unclosed historical range")
    target = db.get(BinanceSpotSymbol, symbol)
    if target is None or not target.active or target.base_asset in STABLECOIN_BASES:
        db.rollback()
        raise ValueError("Active non-stablecoin Binance Spot symbol required")
    db.rollback()
    cursor, pages = start, 0
    while cursor < end:
        payload = _get(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": "15m",
                "startTime": cursor,
                "endTime": end - 1,
                "limit": 1000,
            },
        )
        values = parse_page(payload, symbol, cursor, end)
        if not values:
            break
        save_page(db, values)
        pages += 1
        cursor = values[-1]["open_time"] + BAR_MS
        if progress:
            progress(symbol, pages, len(values))
        if cursor < end:
            sleep(pause)
    opened = list(
        db.scalars(
            select(BinanceSpotCandle.open_time)
            .where(
                BinanceSpotCandle.symbol == symbol,
                BinanceSpotCandle.open_time >= start,
                BinanceSpotCandle.open_time < end,
            )
            .order_by(BinanceSpotCandle.open_time)
        )
    )
    db.rollback()
    expected = (end - start) // BAR_MS
    missing = expected - len(opened)
    return dict(
        symbol=symbol,
        status="complete" if missing == 0 else "incomplete",
        expected_candles=expected,
        stored_candles=len(opened),
        missing_candles=missing,
        pages=pages,
        first_open_ms=opened[0] if opened else None,
        last_open_ms=opened[-1] if opened else None,
    )
