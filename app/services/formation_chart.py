from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.services.formations import BAR, analyze_rows


def chart_data(db, symbol, stamp):
    active = db.get(BinanceSpotSymbol, symbol)
    if active is None or not active.active:
        return None
    rows = list(
        reversed(
            db.query(BinanceSpotCandle)
            .filter(
                BinanceSpotCandle.symbol == symbol,
                BinanceSpotCandle.open_time + BAR <= stamp,
            )
            .order_by(BinanceSpotCandle.open_time.desc())
            .limit(200)
            .all()
        )
    )
    result = analyze_rows(rows, symbol, stamp)
    result["candles"] = (
        []
        if result["status"] == "invalid_data"
        else [
            {
                "open_time": r.open_time,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ]
    )
    return result
