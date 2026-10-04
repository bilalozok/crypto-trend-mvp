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
from app.services.candle_ingestion import upsert_candles
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

    try:
        return upsert_candles(db, symbol, interval, klines)
    except (ValueError, IndexError, TypeError) as exc:
        raise HTTPException(
            status_code=502, detail="Provider returned invalid candle data"
        ) from exc


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
