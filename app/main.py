from fastapi import FastAPI, Depends, Query
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models import Candle

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/candles/latest")
def get_latest_candles(
    symbol: str = Query(..., description="örn: BTCUSDT"),
    interval: str = Query("1h"),
    limit: int = Query(5, ge=1, le=500),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol.upper().strip(),
            Candle.interval == interval.strip(),
        )
        .order_by(Candle.open_time.desc())
        .limit(limit)
        .all()
    )

    # Geçici: ORM serialize problemi olmasın diye dict dön
    return [
        {
            "id": r.id,
            "symbol": r.symbol,
            "interval": r.interval,
            "open_time": r.open_time,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
        }
        for r in rows
    ]
