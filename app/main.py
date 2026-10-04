from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.candle import Candle
from app.db.session import SessionLocal, engine
from app.schemas import CandleOut
from app.schemas.signal import TrendOut
from app.services.binance import fetch_klines
from app.services.signal_engine import get_trend
from app.utils.timeframes import Interval

app = FastAPI(title="Crypto Trend MVP")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbDep = Annotated[Session, Depends(get_db)]


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/")
def root() -> dict:
    return {"ok": True, "service": "crypto-trend-mvp"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/candles/latest", response_model=list[CandleOut])
def get_latest_candles(
    symbol: str = Query(..., description="orn: BTCUSDT"),
    interval: str = Query("1h"),
    limit: int = Query(5, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: Literal["asc", "desc"] = Query("asc"),
    db: DbDep = None,
) -> list[CandleOut]:
    rows = (
        db.query(Candle)
        .filter(Candle.symbol == symbol.upper(), Candle.interval == interval)
        .order_by(Candle.open_time.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return list(reversed(rows)) if sort == "asc" else rows


@app.post("/candles/fetch/{symbol}", response_model=list[CandleOut])
def fetch_candles(
    symbol: str,
    interval: str = Query("1h"),
    limit: int = Query(200, ge=1, le=1000),
    db: DbDep = None,
) -> list[CandleOut]:
    symbol = symbol.upper()

    try:
        klines = fetch_klines(symbol=symbol, interval=interval, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    created_or_updated: list[Candle] = []

    for k in klines:
        open_time_ms = int(k[0])
        open_time_dt = open_time_ms

        open_price = float(k[1])
        high_price = float(k[2])
        low_price = float(k[3])
        close_price = float(k[4])
        volume = float(k[5])

        existing = (
            db.query(Candle)
            .filter(
                Candle.symbol == symbol,
                Candle.interval == interval,
                Candle.open_time == open_time_dt,
            )
            .first()
        )

        if existing:
            existing.open = open_price
            existing.high = high_price
            existing.low = low_price
            existing.close = close_price
            existing.volume = volume
            created_or_updated.append(existing)
        else:
            row = Candle(
                symbol=symbol,
                interval=interval,
                open_time=open_time_dt,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
            )
            db.add(row)
            created_or_updated.append(row)

    db.commit()

    for row in created_or_updated:
        db.refresh(row)

    created_or_updated.sort(key=lambda x: x.open_time)
    return created_or_updated


@app.get("/signals/trend", response_model=TrendOut)
def trend_summary(
    symbol: str = Query(..., min_length=3, max_length=30, pattern="^[A-Za-z0-9]+$"),
    interval: Annotated[Interval, Query()] = "1h",
    short_period: int = Query(5, ge=1, le=499),
    long_period: int = Query(20, ge=2, le=500),
    db: DbDep = None,
) -> TrendOut:
    """Compare SMAs of stored, closed candles; this request does not fetch new data."""
    if short_period >= long_period:
        raise HTTPException(status_code=422, detail="short_period must be less than long_period")
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return get_trend(db, symbol.upper(), interval, short_period, long_period, now_ms)
