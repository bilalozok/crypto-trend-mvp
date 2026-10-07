"""Collect Binance Spot USDT 15m candles; run once under Railway Cron."""

import logging
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from time import monotonic

from app.services.binance_market import BinanceMarketError, catalog
from app.workers.scheduler import load_settings

logger = logging.getLogger(__name__)


def market_error_details(exc):
    """Use fixed labels, never raw responses, request URLs or credentials."""
    labels = {
        "Binance returned no candles": "empty_candles",
        "Invalid Binance 15m candle data": "invalid_15m_candles",
        "No closed 15m candles available": "no_closed_candles",
        "Binance rate limit reached; retry later": "rate_limit",
        "Binance market data is unavailable from this server": "access_denied",
        "Binance market data request failed": "request_failed",
        "Binance returned no valid candles": "empty_candles",
        "Binance returned invalid candle data": "invalid_candles",
        "Symbol is not an active Binance Spot USDT pair": "inactive_symbol",
    }
    reason = (
        labels.get(str(exc), "market_error")
        if isinstance(exc, BinanceMarketError)
        else "internal_error"
    )
    cause = exc.__cause__
    response = getattr(cause, "response", None)
    status = getattr(response, "status_code", None)
    status = status if isinstance(status, int) and 100 <= status <= 599 else None
    return reason, status


def collect_market(budget=180, workers=4, limit=500):
    from sqlalchemy import select, update

    from app.db.models.binance_spot import BinanceSpotSymbol
    from app.db.session import SessionLocal
    from app.services.binance_collection import now_ms, refresh_symbol, sync_symbols
    from app.services.formation_history import record_symbol

    timeframe_enabled = os.getenv("PORTFOLIO_TIMEFRAMES_ENABLED", "false").lower() == "true"
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

    portfolio_symbols = set()
    if timeframe_enabled:
        from app.services.portfolio_feeds import owned_symbols

        with SessionLocal() as db:
            portfolio_symbols = set(owned_symbols(db))

    from app.services.candidate_feeds import tracked_symbols

    with SessionLocal() as db:
        candidate_symbols = tracked_symbols(db, now_ms())
    logger.info("candidate_timeframes_eligible symbols=%s", len(candidate_symbols))
    portfolio_symbols.update(candidate_symbols)

    def fetch(symbol):
        hard_failure = False
        try:
            with SessionLocal() as db:
                count = refresh_symbol(db, symbol, limit)
            logger.info("binance_fetch_complete symbol=%s interval=15m candles=%s", symbol, count)
            try:
                from app.services.candidate_observer import observe_symbol

                with SessionLocal() as db:
                    observed = observe_symbol(db, symbol, now_ms())
                if observed:
                    logger.info(
                        "candidate_observation_complete symbol=%s recorded=%s", symbol, observed
                    )
            except Exception as exc:
                hard_failure = True
                logger.error(
                    "candidate_observation_failed symbol=%s error=%s", symbol, type(exc).__name__
                )

            try:
                with SessionLocal() as db:
                    events = record_symbol(db, symbol, now_ms())
                logger.info("formation_history_complete symbol=%s events=%s", symbol, events)
            except Exception as exc:
                logger.error(
                    "formation_history_failed symbol=%s error=%s", symbol, type(exc).__name__
                )
                return False, False, True
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
                    return False, False, True
            if symbol in portfolio_symbols:
                from app.services.portfolio_feeds import refresh_symbol as refresh_timeframes

                try:
                    with SessionLocal() as db:
                        refresh_timeframes(db, symbol, now_ms())
                    logger.info("portfolio_timeframes_complete symbol=%s", symbol)
                    if symbol in candidate_symbols:
                        logger.info("candidate_timeframes_complete symbol=%s", symbol)
                except Exception as exc:
                    hard_failure = True
                    logger.error(
                        "portfolio_timeframes_failed symbol=%s error=%s", symbol, type(exc).__name__
                    )
                    if isinstance(exc, BinanceMarketError) and exc.status_code == 503:
                        return False, True, True
            try:
                from app.services.portfolio_auto import run_symbol

                with SessionLocal() as db:
                    run_symbol(db, symbol, now_ms())
            except Exception as exc:
                hard_failure = True
                logger.error("portfolio_auto_failed symbol=%s error=%s", symbol, type(exc).__name__)
            return not hard_failure, False, hard_failure
        except Exception as exc:
            with SessionLocal() as db:
                db.execute(
                    update(BinanceSpotSymbol)
                    .where(BinanceSpotSymbol.symbol == symbol)
                    .values(last_attempt_ms=now_ms(), last_error=type(exc).__name__)
                )
                db.commit()
            reason, http_status = market_error_details(exc)
            logger.error(
                "binance_fetch_failed symbol=%s error=%s reason=%s http_status=%s mapped_status=%s",
                symbol,
                type(exc).__name__,
                reason,
                http_status,
                exc.status_code if isinstance(exc, BinanceMarketError) else None,
            )
            blocked_error = isinstance(exc, BinanceMarketError) and exc.status_code == 503
            return False, blocked_error, not isinstance(exc, BinanceMarketError) or blocked_error

    pending_symbols = iter(symbols)
    successes, failures, submitted, hard_failures = 0, 0, 0, 0
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
                ok, stop, hard = future.result()
                successes += int(ok)
                failures += int(not ok)
                hard_failures += int(hard)
                blocked = blocked or stop
    try:
        from app.services.candidate_auto import settle_due

        with SessionLocal() as db:
            settle_due(db, now_ms())
    except Exception as exc:
        hard_failures += 1
        logger.error("candidate_auto_failed error=%s", type(exc).__name__)
    fatal = bool(hard_failures or blocked or (failures and not successes))
    outcome = "failed" if fatal else "partial" if failures else "complete"
    logger.info(
        "binance_market_complete total=%s attempted=%s success=%s failed=%s deferred=%s "
        "outcome=%s hard_failures=%s",
        len(symbols),
        submitted,
        successes,
        failures,
        len(symbols) - submitted,
        outcome,
        hard_failures,
    )
    return 1 if fatal else 0


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
