"""Bounded public 4h/1d collection for purchase symbols, independent of FX."""

import math

import requests
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models.account import Account, Purchase
from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.portfolio_timeframe import PortfolioCandle, PortfolioFeed
from app.services.binance_market import BASE_URL, BinanceMarketError
from app.services.portfolio_timeframes import INTERVALS


def owned_symbols(db):
    return list(
        db.scalars(
            select(Purchase.symbol)
            .join(Account, Account.id == Purchase.account_id)
            .join(BinanceSpotSymbol, BinanceSpotSymbol.symbol == Purchase.symbol)
            .where(Account.active.is_(True), BinanceSpotSymbol.active.is_(True))
            .distinct()
            .order_by(Purchase.symbol)
            .limit(500)
        ).all()
    )


def parse(payload, symbol, interval, stamp):
    bar = INTERVALS[interval]
    if not isinstance(payload, list) or not 1 <= len(payload) <= 201:
        raise BinanceMarketError("Invalid portfolio candle shape")
    values = {}
    try:
        for row in payload:
            if not isinstance(row, list) or len(row) < 7:
                raise ValueError
            opened, closed = row[0], row[6]
            prices = [float(row[i]) for i in range(1, 6)]
            if (
                type(opened) is not int
                or type(closed) is not int
                or opened < 0
                or opened % bar
                or closed != opened + bar - 1
                or any(not math.isfinite(p) or p <= 0 for p in prices[:4])
                or not math.isfinite(prices[4])
                or prices[4] < 0
                or prices[1] < max(prices[0], prices[2], prices[3])
                or prices[2] > min(prices[0], prices[1], prices[3])
            ):
                raise ValueError
            if opened + bar > stamp:
                continue
            if opened in values:
                raise ValueError
            values[opened] = dict(
                symbol=symbol,
                interval=interval,
                open_time=opened,
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
                volume=prices[4],
            )
        ordered = sorted(values)
        if any(b - a != bar for a, b in zip(ordered, ordered[1:], strict=False)):
            raise ValueError
        if not values or max(values) != stamp // bar * bar - bar:
            raise ValueError
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise BinanceMarketError("Invalid or stale portfolio candles") from exc
    return list(values.values())


def refresh(db, symbol, interval, stamp):
    bar = INTERVALS[interval]
    target = stamp // bar * bar
    state = db.get(PortfolioFeed, (symbol, interval))
    if state and state.last_success_ms is not None and state.last_success_ms >= target:
        return dict(status="cached", candles=0)
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    reservation = insert(PortfolioFeed).values(
        symbol=symbol, interval=interval, last_attempt_ms=stamp
    )
    reservation = reservation.on_conflict_do_update(
        index_elements=["symbol", "interval"],
        set_={"last_attempt_ms": stamp},
        where=PortfolioFeed.last_attempt_ms < stamp - 60_000,
    )
    reserved = db.execute(reservation).rowcount
    db.commit()
    if not reserved:
        return dict(status="throttled", candles=0)
    try:
        response = requests.get(
            BASE_URL + "/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": 201},
            timeout=(3, 8),
            allow_redirects=False,
        )
        if response.status_code != 200:
            code = 503 if response.status_code in (418, 429, 403, 451) else 502
            raise BinanceMarketError("Portfolio feed HTTP " + str(response.status_code), code)
        values = parse(response.json(), symbol, interval, stamp)
        statement = insert(PortfolioCandle).values(values)
        db.execute(
            statement.on_conflict_do_update(
                index_elements=["symbol", "interval", "open_time"],
                set_={
                    key: getattr(statement.excluded, key)
                    for key in ("open", "high", "low", "close", "volume")
                },
            )
        )
        db.execute(
            update(PortfolioFeed)
            .where(PortfolioFeed.symbol == symbol, PortfolioFeed.interval == interval)
            .values(last_success_ms=stamp, last_error=None)
        )
        db.commit()
        return dict(status="updated", candles=len(values))
    except Exception as exc:
        db.rollback()
        db.execute(
            update(PortfolioFeed)
            .where(PortfolioFeed.symbol == symbol, PortfolioFeed.interval == interval)
            .values(last_error=type(exc).__name__)
        )
        db.commit()
        if isinstance(exc, BinanceMarketError):
            raise
        if isinstance(exc, (requests.RequestException, ValueError)):
            raise BinanceMarketError("Portfolio feed unavailable") from exc
        raise


def refresh_symbol(db, symbol, stamp):
    result = {}
    for interval in INTERVALS:
        result[interval] = refresh(db, symbol, interval, stamp)
    return result
