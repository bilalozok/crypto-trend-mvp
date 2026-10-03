from typing import Annotated

import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.models import Candle
from app.schemas import CandleOut

app = FastAPI(title="Crypto Trend MVP")

Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DBSession = Annotated[Session, Depends(get_db)]


@app.get("/")
def root():
    return {"ok": True, "service": "crypto-trend-mvp", "build": "af1f76f-marker"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/candles/latest", response_model=list[CandleOut])
def get_latest_candles(
    symbol: str = Query(..., description="örn: BTCUSDT"),
    interval: str = Query("1h"),
    limit: int = Query(5, ge=1, le=500),
    db: DBSession = None,
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
    return rows


@app.post("/candles/fetch/{symbol}")
def fetch_candles(
    symbol: str,
    interval: str = Query("1h"),
    limit: int = Query(200, ge=1, le=1000),
    db: DBSession = None,
):
    symbol = symbol.upper().strip()
    interval = interval.strip()

    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as err:
        raise HTTPException(
            status_code=502,
            detail=f"Binance request failed: {err}",
        ) from err

    inserted = 0
    skipped_existing = 0

    for kline in data:
        open_time = int(kline[0])

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

        db.add(
            Candle(
                symbol=symbol,
                interval=interval,
                open_time=open_time,
                open=float(kline[1]),
                high=float(kline[2]),
                low=float(kline[3]),
                close=float(kline[4]),
                volume=float(kline[5]),
                close_time=int(kline[6]),
            )
        )
        inserted += 1

    db.commit()

    return {
        "symbol": symbol,
        "interval": interval,
        "received": len(data),
        "inserted": inserted,
        "skipped_existing": skipped_existing,
    }
