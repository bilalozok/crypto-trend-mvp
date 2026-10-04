from app.db.session import SessionLocal
from app.services.binance import fetch_klines
from app.services.candle_ingestion import upsert_candles


def refresh_candles(symbol: str, interval: str, limit: int) -> int:
    klines = fetch_klines(symbol=symbol, interval=interval, limit=limit)
    with SessionLocal() as db:
        return len(upsert_candles(db, symbol, interval, klines))
