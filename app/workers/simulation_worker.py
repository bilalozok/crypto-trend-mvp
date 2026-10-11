"""Run once every five minutes; download prices only for live simulations."""

import logging
import math
from time import monotonic

from sqlalchemy import func, select, text

from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.simulation import Simulation, SimulationCandle
from app.services.binance_collection import insert_for, now_ms
from app.services.binance_market import BinanceMarketError, _get
from app.services.simulation import EXECUTION_BAR as BAR
from app.workers.scheduler import load_settings

logger = logging.getLogger(__name__)


def parse(payload, symbol, stamp):
    if not isinstance(payload, list) or not payload or len(payload) > 1000:
        raise ValueError("Invalid 5m candles")
    values = []
    seen = set()
    for row in payload:
        if not isinstance(row, list) or len(row) < 7:
            raise ValueError("Invalid 5m candle")
        opened, closed = row[0], row[6]
        if (
            type(opened) is not int
            or type(closed) is not int
            or opened < 0
            or opened % BAR
            or closed != opened + BAR - 1
            or opened in seen
        ):
            raise ValueError("Invalid 5m timestamps")
        seen.add(opened)
        prices = [float(v) for v in row[1:6]]
        o, h, low, c, v = prices
        if (
            not all(math.isfinite(p) for p in prices)
            or not 0 < low <= min(o, c) <= max(o, c) <= h <= 1e12
            or v < 0
        ):
            raise ValueError("Invalid 5m prices")
        if opened + BAR <= stamp:
            values.append(
                dict(symbol=symbol, open_time=opened, open=o, high=h, low=low, close=c, volume=v)
            )
    if not values or max(v["open_time"] for v in values) != stamp // BAR * BAR - BAR:
        raise ValueError("Latest closed 5m candle missing")
    return values


def collect(session_factory, budget=90, limit=100):
    deadline = monotonic() + budget
    stamp = now_ms()
    latest = (
        select(SimulationCandle.symbol, func.max(SimulationCandle.open_time).label("last"))
        .group_by(SimulationCandle.symbol)
        .subquery()
    )
    with session_factory() as db:
        symbols = db.scalars(
            select(Simulation.symbol, latest.c.last)
            .distinct()
            .join(BinanceSpotSymbol, BinanceSpotSymbol.symbol == Simulation.symbol)
            .outerjoin(latest, latest.c.symbol == Simulation.symbol)
            .where(
                Simulation.state.in_(("pending_entry", "open", "pending_exit")),
                BinanceSpotSymbol.active.is_(True),
            )
            .order_by(latest.c.last.asc().nullsfirst(), Simulation.symbol)
            .limit(limit)
        ).all()
    failed = 0
    completed = 0
    for symbol in symbols:
        if monotonic() >= deadline:
            break
        with session_factory() as db:
            last = db.scalar(
                select(func.max(SimulationCandle.open_time)).where(
                    SimulationCandle.symbol == symbol
                )
            )
        target = stamp // BAR * BAR - BAR
        if last is not None and last >= target:
            continue
        # Fetch up to 1000 bars on bootstrap; incremental requests overlap one bar.
        params = dict(symbol=symbol, interval="5m", limit=1000)
        if last is not None:
            params["startTime"] = max(last - BAR, target - 998 * BAR)
        try:
            values = parse(_get("/api/v3/klines", params), symbol, stamp)
            with session_factory() as db:
                statement = insert_for(db, SimulationCandle).values(values)
                db.execute(
                    statement.on_conflict_do_update(
                        index_elements=["symbol", "open_time"],
                        set_={
                            name: getattr(statement.excluded, name)
                            for name in ("open", "high", "low", "close", "volume")
                        },
                    )
                )
                db.commit()
            completed += 1
        except BinanceMarketError:
            failed += 1
            logger.warning("simulation_5m_market_unavailable")
            break  # Do not multiply rate-limit or regional access failures.
        except (ValueError, TypeError, OverflowError):
            failed += 1
            logger.warning("simulation_5m_invalid_candles symbol=%s", symbol)
    logger.info(
        "simulation_5m_complete eligible=%s updated=%s failed=%s", len(symbols), completed, failed
    )
    return 1 if failed else 0


def main():
    logging.basicConfig(level=logging.INFO)
    load_settings()
    from app.db.session import SessionLocal, engine

    try:
        with engine.connect() as connection:
            # Session-level lock stays held while separate ingestion transactions commit.
            if engine.dialect.name == "postgresql":
                acquired = connection.scalar(text("SELECT pg_try_advisory_lock(51020265)"))
                if not acquired:
                    logger.info("simulation_5m_already_running")
                    return 0
            try:
                return collect(SessionLocal)
            finally:
                if engine.dialect.name == "postgresql":
                    connection.execute(text("SELECT pg_advisory_unlock(51020265)"))
    except Exception as exc:
        logger.error("simulation_5m_failed type=%s", type(exc).__name__)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
