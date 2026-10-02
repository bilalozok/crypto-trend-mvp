from sqlalchemy import select
from typing import Optional, Literal,  List
import requests
from fastapi import FastAPI, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models.candle import Candle

app = FastAPI(title="Crypto Trend MVP")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CandleOut(BaseModel):
    id: int
    symbol: str
    interval: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    class Config:
        from_attributes = True


@app.get("/")
def root():
    return {"status": "ok"}


@app.get(
    "/candles/latest",
    summary="Son mum verilerini listeler",
    description=(
        "Mum verilerini symbol/interval filtreleriyle getirir. "
        "open_time alanına göre asc/desc sıralama ve limit/offset pagination destekler."
    ),
)
def get_latest_candles(
    symbol: Optional[str] = Query(
        default=None,
        description="Sembol filtresi (örn: BTCUSDT)"
    ),
    interval: Optional[str] = Query(
        default=None,
        description="Interval filtresi (örn: 1m, 5m, 1h)"
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Dönecek kayıt sayısı (1-1000, varsayılan: 100)"
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Atlanacak kayıt sayısı (varsayılan: 0)"
    ),
    sort: Literal["asc", "desc"] = Query(
        default="desc",
        description="open_time sıralama yönü: asc | desc (varsayılan: desc)"
    ),
    db: Session = Depends(get_db),
):
    q = select(Candle)

    if symbol:
        q = q.where(Candle.symbol == symbol.upper())
    if interval:
        q = q.where(Candle.interval == interval)

    q = q.order_by(Candle.open_time.asc() if sort == "asc" else Candle.open_time.desc())
    q = q.offset(offset).limit(limit)

    rows = db.execute(q).scalars().all()
    return rows



@app.get("/candles/latest_raw")
def candles_latest_raw(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 5,
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Candle)
        .filter(Candle.symbol == symbol.upper().strip(), Candle.interval == interval.strip())
        .order_by(Candle.open_time.desc())
        .limit(limit)
        .all()
    )
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


@app.get("/debug/db")
def debug_db(db: Session = Depends(get_db)):
    total = db.query(Candle).count()
    last = db.query(Candle).order_by(Candle.id.desc()).first()
    return {
        "total": total,
        "last": (
            {
                "id": last.id,
                "symbol": last.symbol,
                "interval": last.interval,
                "open_time": last.open_time,
                "close": last.close,
            }
            if last
            else None
        ),
    }


@app.post("/candles/fetch/{symbol}")
def fetch_candles(
    symbol: str,
    interval: str = Query("1h"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    symbol = symbol.upper().strip()
    interval = interval.strip()

    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Binance request failed: {e}")

    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail=f"Unexpected Binance response: {data}")

    inserted = 0
    skipped_existing = 0

    for k in data:
        open_time = int(k[0])

        exists = (
            db.query(Candle)
            .filter(
                Candle.symbol == symbol,
                Candle.interval == interval,
                Candle.open_time == open_time,
            )
            .first()
        )
        if exists:
            skipped_existing += 1
            continue

        row = Candle(
            symbol=symbol,
            interval=interval,
            open_time=open_time,
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        )
        db.add(row)
        inserted += 1

    db.commit()

    return {
        "symbol": symbol,
        "interval": interval,
        "requested": limit,
        "received": len(data),
        "inserted": inserted,
        "skipped_existing": skipped_existing,
    }
