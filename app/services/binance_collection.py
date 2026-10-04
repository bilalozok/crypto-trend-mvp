from datetime import UTC, datetime
from math import isfinite

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.services.binance_market import BinanceMarketError, _get

BAR_MS = 900_000


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def insert_for(db, model):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '10s'"))
        db.execute(text("SET LOCAL statement_timeout = '30s'"))
        return pg_insert(model)
    return sqlite_insert(model)


def sync_symbols(db: Session, rows, stamp: int):
    if not rows:
        raise ValueError("Empty catalogue must not deactivate the market")
    values = [
        dict(
            symbol=r.symbol,
            base_asset=r.base_asset,
            quote_volume_24h=r.quote_volume_24h,
            active=True,
            catalog_updated_ms=stamp,
        )
        for r in rows
    ]
    statement = insert_for(db, BinanceSpotSymbol).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            key: getattr(statement.excluded, key)
            for key in ("base_asset", "quote_volume_24h", "active", "catalog_updated_ms")
        },
    )
    try:
        db.execute(statement)
        db.execute(
            update(BinanceSpotSymbol)
            .where(BinanceSpotSymbol.symbol.not_in([r.symbol for r in rows]))
            .values(active=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def refresh_symbol(db: Session, symbol: str, limit: int = 500) -> int:
    latest = db.scalar(
        select(func.max(BinanceSpotCandle.open_time)).where(BinanceSpotCandle.symbol == symbol)
    )
    params = {"symbol": symbol, "interval": "15m", "limit": limit}
    if latest is not None:
        params["startTime"] = max(0, latest - BAR_MS)
    # Release the read transaction before the external network request.
    db.rollback()
    payload = _get("/api/v3/klines", params)
    stamp = now_ms()
    if not isinstance(payload, list) or not payload:
        raise BinanceMarketError("Binance returned no candles")
    values = {}
    try:
        for row in payload:
            opened, closed = int(row[0]), int(row[6])
            prices = [float(row[i]) for i in range(1, 6)]
            if (
                opened < 0
                or opened % BAR_MS
                or closed != opened + BAR_MS - 1
                or any(not isfinite(p) or p <= 0 for p in prices[:4])
                or not isfinite(prices[4])
                or prices[4] < 0
                or prices[1] < max(prices[0], prices[2], prices[3])
                or prices[2] > min(prices[0], prices[1], prices[3])
            ):
                raise ValueError
            if closed >= stamp:
                continue
            values[opened] = dict(
                symbol=symbol,
                interval="15m",
                open_time=opened,
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
                volume=prices[4],
            )
    except (ValueError, TypeError, IndexError) as exc:
        raise BinanceMarketError("Invalid Binance 15m candle data") from exc
    if not values:
        raise BinanceMarketError("No closed 15m candles available")
    statement = insert_for(db, BinanceSpotCandle).values(list(values.values()))
    statement = statement.on_conflict_do_update(
        index_elements=["symbol", "open_time"],
        set_={
            key: getattr(statement.excluded, key)
            for key in ("open", "high", "low", "close", "volume")
        },
    )
    try:
        db.execute(statement)
        db.execute(
            update(BinanceSpotSymbol)
            .where(BinanceSpotSymbol.symbol == symbol)
            .values(
                last_attempt_ms=stamp,
                last_success_ms=stamp,
                last_error=None,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(values)
