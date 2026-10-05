"""Collect Binance Spot USDT 15m candles; run once under Railway Cron."""

import logging
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from time import monotonic

from app.services.binance_market import BinanceMarketError, catalog
from app.workers.scheduler import load_settings

logger = logging.getLogger(__name__)


def collect_market(budget=180, workers=4, limit=500):
    from sqlalchemy import select, update

    from app.db.models.binance_spot import BinanceSpotSymbol
    from app.db.session import SessionLocal
    from app.services.binance_collection import now_ms, refresh_symbol, sync_symbols
    from app.services.formation_history import record_symbol

    deadline = monotonic() + budget
    _, rows = catalog.snapshot()
    with SessionLocal() as db:
        sync_symbols(db, rows, now_ms())
        symbols = list(
            db.scalars(
                select(BinanceSpotSymbol.symbol)
                .where(BinanceSpotSymbol.active.is_(True))
                .order_by(
                    BinanceSpotSymbol.last_attempt_ms.asc().nullsfirst(), BinanceSpotSymbol.symbol
                )
            )
        )

    def fetch(symbol):
        try:
            with SessionLocal() as db:
                count = refresh_symbol(db, symbol, limit)
            logger.info("binance_fetch_complete symbol=%s interval=15m candles=%s", symbol, count)
            try:
                with SessionLocal() as db:
                    events = record_symbol(db, symbol, now_ms())
                logger.info("formation_history_complete symbol=%s events=%s", symbol, events)
            except Exception as exc:
                logger.error(
                    "formation_history_failed symbol=%s error=%s", symbol, type(exc).__name__
                )
                return False, False
            if os.getenv("FORWARD_TRACKING_ENABLED", "false").lower() == "true":
                from app.services.forward_tracking import track_symbol

                try:
                    with SessionLocal() as db:
                        tracked = track_symbol(db, symbol, now_ms())
                    logger.info(
                        "forward_tracking_complete symbol=%s recorded=%s settled=%s",
                        symbol,
                        tracked["recorded"],
                        tracked["settled"],
                    )
                except Exception as exc:
                    logger.error(
                        "forward_tracking_failed symbol=%s error=%s", symbol, type(exc).__name__
                    )
                    return False, False
            return True, False
        except Exception as exc:
            with SessionLocal() as db:
                db.execute(
                    update(BinanceSpotSymbol)
                    .where(BinanceSpotSymbol.symbol == symbol)
                    .values(last_attempt_ms=now_ms(), last_error=type(exc).__name__)
                )
                db.commit()
            logger.error("binance_fetch_failed symbol=%s error=%s", symbol, type(exc).__name__)
            return False, isinstance(exc, BinanceMarketError) and exc.status_code == 503

    pending_symbols = iter(symbols)
    successes, failures, submitted = 0, 0, 0
    blocked = False
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = set()
        while True:
            while len(pending) < workers and not blocked and monotonic() < deadline:
                symbol = next(pending_symbols, None)
                if symbol is None:
                    break
                pending.add(executor.submit(fetch, symbol))
                submitted += 1
            if not pending:
                break
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                ok, stop = future.result()
                successes += int(ok)
                failures += int(not ok)
                blocked = blocked or stop
    logger.info(
        "binance_market_complete total=%s attempted=%s success=%s failed=%s deferred=%s",
        len(symbols),
        submitted,
        successes,
        failures,
        len(symbols) - submitted,
    )
    return 1 if failures else 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        load_settings()  # Validate and normalize DATABASE_URL before ORM imports.
        budget = int(os.getenv("MARKET_BUDGET_SECONDS", "180"))
        workers = int(os.getenv("MARKET_CONCURRENCY", "4"))
        limit = int(os.getenv("MARKET_HISTORY_LIMIT", "500"))
        if not 30 <= budget <= 180 or not 1 <= workers <= 4 or not 50 <= limit <= 1000:
            raise ValueError
    except ValueError:
        logger.error("Invalid market worker configuration")
        return 2
    from app.db.base import engine as base_engine
    from app.db.session import engine

    try:
        return collect_market(budget, workers, limit)
    except Exception as exc:
        logger.error("binance_market_failed error=%s", type(exc).__name__)
        return 1
    finally:
        engine.dispose()
        base_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
