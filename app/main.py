from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candle import Candle
from app.db.session import SessionLocal, engine
from app.schemas import CandleOut
from app.schemas.market import (
    BinancePreviewOut,
    CollectionCoverageOut,
    SpotCatalogOut,
    StoredBinanceCandlesOut,
)
from app.schemas.signal import TrendOut
from app.services.binance import fetch_klines
from app.services.binance_collection import now_ms
from app.services.binance_coverage import coverage
from app.services.binance_market import BinanceMarketError, catalog, preview_candles
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
    if engine.dialect.name == "sqlite":
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


@app.get("/market/binance/symbols", response_model=SpotCatalogOut)
def binance_spot_symbols(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    min_quote_volume: float = Query(0, ge=0, allow_inf_nan=False),
) -> SpotCatalogOut:
    """Active Binance Spot USDT pairs, ordered by rolling 24h USDT volume."""
    try:
        as_of, rows = catalog.snapshot()
    except BinanceMarketError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    filtered = [row for row in rows if row.quote_volume_24h >= min_quote_volume]
    return SpotCatalogOut(
        as_of=as_of,
        min_quote_volume=min_quote_volume,
        total=len(filtered),
        limit=limit,
        offset=offset,
        symbols=filtered[offset : offset + limit],
    )


@app.get("/market/binance/candles/preview", response_model=BinancePreviewOut)
def binance_candle_preview(
    symbol: str = Query(..., min_length=3, max_length=64),
    limit: int = Query(25, ge=2, le=100),
) -> BinancePreviewOut:
    """Read closed 15m Binance candles without storing or mixing legacy data."""
    try:
        return preview_candles(symbol, limit)
    except BinanceMarketError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/market/binance/coverage", response_model=CollectionCoverageOut)
def binance_collection_coverage(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    candles_required: int = Query(200, ge=1, le=1000),
    db: DbDep = None,
) -> CollectionCoverageOut:
    try:
        return CollectionCoverageOut.model_validate(
            coverage(db, limit, offset, candles_required, now_ms())
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Binance collection database unavailable"
        ) from exc


@app.get("/market/binance/candles", response_model=StoredBinanceCandlesOut)
def stored_binance_candles(
    symbol: str = Query(..., min_length=3, max_length=64),
    limit: int = Query(100, ge=1, le=1000),
    db: DbDep = None,
) -> StoredBinanceCandlesOut:
    symbol = symbol.upper()
    try:
        rows = (
            db.query(BinanceSpotCandle)
            .filter(BinanceSpotCandle.symbol == symbol)
            .order_by(BinanceSpotCandle.open_time.desc())
            .limit(limit)
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Binance collection database unavailable"
        ) from exc
    return StoredBinanceCandlesOut(symbol=symbol, candles=list(reversed(rows)))


@app.get("/analysis/binance/formations")
def formation_analysis(
    db: DbDep,
    symbol: str = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$"),
):
    from app.services.formations import analyze

    try:
        result = analyze(db, symbol.upper(), now_ms())
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Analysis database unavailable") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Active Binance Spot USDT symbol not found")
    return result


@app.get("/analysis/binance/formations/scan")
def formation_scan(
    db: DbDep,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    direction: Literal["all", "up", "down"] = Query("all"),
    state: Literal["all", "forming", "confirmed", "invalidated"] = Query("confirmed"),
    min_quote_volume: float = Query(0, ge=0, allow_inf_nan=False),
    include_stablecoins: bool = Query(False),
):
    from app.services.formation_scan import scan

    try:
        return scan(
            db,
            now_ms(),
            limit,
            offset,
            direction,
            state,
            min_quote_volume,
            include_stablecoins,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Analysis database unavailable") from exc
