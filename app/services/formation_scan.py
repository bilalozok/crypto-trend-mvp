from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.services.binance_coverage import STABLECOIN_BASES
from app.services.formations import BAR, analyze_rows, timestamp


def scan(
    db,
    stamp,
    limit=50,
    offset=0,
    direction="all",
    state="confirmed",
    min_volume=0,
    include_stablecoins=False,
    max_confirmation_age_bars=None,
    breakout_holding=None,
    min_volume_ratio=None,
    pattern=None,
):
    filters = [
        BinanceSpotSymbol.active.is_(True),
        BinanceSpotSymbol.quote_volume_24h >= min_volume,
    ]
    if not include_stablecoins:
        filters.append(BinanceSpotSymbol.base_asset.not_in(STABLECOIN_BASES))
    total = db.scalar(select(func.count()).select_from(BinanceSpotSymbol).where(*filters))
    symbols = db.scalars(
        select(BinanceSpotSymbol)
        .where(*filters)
        .order_by(BinanceSpotSymbol.quote_volume_24h.desc(), BinanceSpotSymbol.symbol)
        .offset(offset)
        .limit(limit)
    ).all()
    grouped = defaultdict(list)
    if symbols:
        ranked = (
            select(
                BinanceSpotCandle,
                func.row_number()
                .over(
                    partition_by=BinanceSpotCandle.symbol,
                    order_by=BinanceSpotCandle.open_time.desc(),
                )
                .label("position"),
            )
            .where(
                BinanceSpotCandle.symbol.in_([s.symbol for s in symbols]),
                BinanceSpotCandle.open_time + BAR <= stamp,
            )
            .subquery()
        )
        candle = aliased(BinanceSpotCandle, ranked)
        rows = db.scalars(
            select(candle).where(ranked.c.position <= 200).order_by(candle.symbol, candle.open_time)
        ).all()
        for row in rows:
            grouped[row.symbol].append(row)
    quality = Counter()
    matches = []
    for symbol in symbols:
        result = analyze_rows(grouped[symbol.symbol], symbol.symbol, stamp)
        quality[result["status"]] += 1
        patterns = [
            p
            for p in result["patterns"]
            if p["status"] != "not_detected"
            and (pattern is None or p["pattern"] == pattern)
            and (state == "all" or p["status"] == state)
            and (direction == "all" or p["direction"] == direction)
            and (
                max_confirmation_age_bars is None
                or (
                    p.get("confirmation_age_bars") is not None
                    and p["confirmation_age_bars"] <= max_confirmation_age_bars
                )
            )
            and (breakout_holding is None or p.get("breakout_holding") is breakout_holding)
            and (
                min_volume_ratio is None
                or (p.get("volume_ratio") is not None and p["volume_ratio"] >= min_volume_ratio)
            )
        ]
        if patterns:
            matches.append(
                {
                    "symbol": symbol.symbol,
                    "quote_volume_24h": symbol.quote_volume_24h,
                    "stablecoin_candidate": symbol.base_asset in STABLECOIN_BASES,
                    "patterns": patterns,
                }
            )
    next_offset = offset + len(symbols)
    return {
        "exchange": "binance",
        "market": "spot",
        "interval": "15m",
        "method_version": "price_patterns_v1",
        "experimental": True,
        "as_of": timestamp(stamp),
        "pattern": pattern,
        "direction": direction,
        "state": state,
        "min_quote_volume": min_volume,
        "include_stablecoins": include_stablecoins,
        "max_confirmation_age_bars": max_confirmation_age_bars,
        "breakout_holding": breakout_holding,
        "min_volume_ratio": min_volume_ratio,
        "total_eligible_symbols": total,
        "limit": limit,
        "offset": offset,
        "scanned_symbols": len(symbols),
        "quality_counts": dict(quality),
        "next_offset": next_offset if next_offset < total else None,
        "matched_symbols": len(matches),
        "matches": matches,
    }
