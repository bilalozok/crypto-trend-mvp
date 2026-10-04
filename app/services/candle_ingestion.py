from math import isfinite

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models.candle import Candle


def upsert_candles(db: Session, symbol: str, interval: str, klines: list) -> list[Candle]:
    """Insert or update candles atomically, including concurrent API/worker writes."""
    symbol = symbol.upper()
    values = {}
    for row in klines:
        timestamp = int(row[0])
        values[timestamp] = {
            "symbol": symbol,
            "interval": interval,
            "open_time": timestamp,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        if timestamp < 0 or any(
            not isfinite(values[timestamp][column])
            for column in ("open", "high", "low", "close", "volume")
        ):
            raise ValueError("Provider returned invalid candle data")
    if not values:
        raise ValueError("Provider returned no candles")

    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(Candle).values(list(values.values()))
    elif dialect == "sqlite":
        statement = sqlite_insert(Candle).values(list(values.values()))
    else:
        raise ValueError(f"Unsupported database dialect: {dialect}")
    statement = statement.on_conflict_do_update(
        index_elements=["symbol", "interval", "open_time"],
        set_={
            column: getattr(statement.excluded, column)
            for column in ("open", "high", "low", "close", "volume")
        },
    )
    try:
        if dialect == "postgresql":
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            db.execute(text("SET LOCAL statement_timeout = '30s'"))
        db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return list(
        db.scalars(
            select(Candle)
            .where(
                Candle.symbol == symbol,
                Candle.interval == interval,
                Candle.open_time.in_(values),
            )
            .order_by(Candle.open_time)
            .execution_options(populate_existing=True)
        ).all()
    )
