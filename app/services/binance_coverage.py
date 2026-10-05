from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.services.binance_collection import BAR_MS

# An explicit starting list, not an automatic classification of every asset.
STABLECOIN_BASES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "USDD", "PYUSD", "USDS", "USD1"}


def coverage(db, limit, offset, required, stamp):
    grouped = (
        select(
            BinanceSpotCandle.symbol.label("symbol"),
            func.count().label("count"),
            func.min(BinanceSpotCandle.open_time).label("oldest"),
            func.max(BinanceSpotCandle.open_time).label("latest"),
        )
        .group_by(BinanceSpotCandle.symbol)
        .subquery()
    )
    total = db.scalar(
        select(func.count())
        .select_from(BinanceSpotSymbol)
        .where(BinanceSpotSymbol.active.is_(True))
    )
    rows = db.execute(
        select(BinanceSpotSymbol, grouped.c.count, grouped.c.oldest, grouped.c.latest)
        .outerjoin(grouped, grouped.c.symbol == BinanceSpotSymbol.symbol)
        .where(BinanceSpotSymbol.active.is_(True))
        .order_by(BinanceSpotSymbol.quote_volume_24h.desc(), BinanceSpotSymbol.symbol)
        .offset(offset)
        .limit(limit)
    ).all()
    expected = stamp // BAR_MS * BAR_MS - BAR_MS
    result = []
    for symbol, count, oldest, latest in rows:
        count = count or 0
        missing = 0 if count == 0 else (latest - oldest) // BAR_MS + 1 - count
        result.append(
            {
                "symbol": symbol.symbol,
                "base_asset": symbol.base_asset,
                "quote_volume_24h": symbol.quote_volume_24h,
                "stablecoin_candidate": symbol.base_asset in STABLECOIN_BASES,
                "candles_count": count,
                "history_sufficient": count >= required,
                "missing_candles_in_stored_range": missing,
                "stale": latest is None or latest < expected,
                "latest_candle_open_time": (
                    None if latest is None else datetime.fromtimestamp(latest / 1000, tz=UTC)
                ),
                "last_success_time": (
                    None
                    if symbol.last_success_ms is None
                    else datetime.fromtimestamp(symbol.last_success_ms / 1000, tz=UTC)
                ),
                "last_error": symbol.last_error,
            }
        )
    return {
        "exchange": "binance",
        "market": "spot",
        "interval": "15m",
        "as_of": datetime.fromtimestamp(stamp / 1000, tz=UTC),
        "total": total,
        "limit": limit,
        "offset": offset,
        "candles_required": required,
        "symbols": result,
    }
