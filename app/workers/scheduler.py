"""One-shot worker: scheduling is handled by Railway Cron."""

import logging
import os
import re
from dataclasses import dataclass

from sqlalchemy.engine import make_url

from app.utils.timeframes import INTERVAL_MS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchSettings:
    symbols: tuple[str, ...]
    intervals: tuple[str, ...]
    limit: int


def load_settings() -> FetchSettings:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url or "${{" in database_url:
        raise ValueError("DATABASE_URL must be explicitly configured and resolved")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    try:
        url = make_url(database_url)
        if url.get_backend_name() not in ("postgresql", "sqlite"):
            raise ValueError
        if url.get_backend_name() == "postgresql":
            url = url.update_query_dict({"connect_timeout": "10"})
            database_url = url.render_as_string(hide_password=False)
    except Exception:
        raise ValueError("DATABASE_URL must be a valid PostgreSQL or SQLite URL") from None
    os.environ["DATABASE_URL"] = database_url

    symbols = tuple(
        dict.fromkeys(s.strip().upper() for s in os.getenv("FETCH_SYMBOLS", "BTCUSDT").split(","))
    )
    intervals = tuple(
        dict.fromkeys(s.strip() for s in os.getenv("FETCH_INTERVALS", "1h").split(","))
    )
    if not symbols or any(not re.fullmatch(r"[A-Z0-9]{3,30}", s) for s in symbols):
        raise ValueError("FETCH_SYMBOLS must contain comma-separated symbols, e.g. BTCUSDT")
    if not intervals or any(s not in INTERVAL_MS for s in intervals):
        raise ValueError("FETCH_INTERVALS contains an unsupported interval")
    if len(symbols) * len(intervals) > 5:
        raise ValueError("At most 5 symbol/interval pairs are supported per run")
    limit = int(os.getenv("FETCH_LIMIT", "100"))
    if not 1 <= limit <= 100:
        raise ValueError("FETCH_LIMIT must be between 1 and 100")
    return FetchSettings(symbols, intervals, limit)


def run_once(settings: FetchSettings) -> int:
    # Import after settings validation to prevent an accidental default SQLite database.
    from app.db.base import engine as base_engine
    from app.db.session import engine
    from app.workers.tasks import refresh_candles

    failures = 0
    try:
        for symbol in settings.symbols:
            for interval in settings.intervals:
                try:
                    count = refresh_candles(symbol, interval, settings.limit)
                    logger.info(
                        "fetch_complete symbol=%s interval=%s candles=%s", symbol, interval, count
                    )
                except Exception as exc:
                    failures += 1
                    # Exception messages can contain database credentials; log only the type.
                    logger.error(
                        "fetch_failed symbol=%s interval=%s error=%s",
                        symbol,
                        interval,
                        type(exc).__name__,
                    )
    finally:
        engine.dispose()
        base_engine.dispose()
    return 1 if failures else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings()
    except ValueError:
        logger.error("Invalid worker configuration; check DATABASE_URL and FETCH_* variables")
        return 2
    return run_once(settings)


if __name__ == "__main__":
    raise SystemExit(main())
