from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import app.db.models.formation_history  # noqa: F401
import app.db.models.forward_report  # noqa: F401
import app.db.models.forward_signal  # noqa: F401
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


@app.get("/analysis/binance/backtest")
def historical_signal_test(
    db: DbDep,
    symbol: str = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$"),
    history_limit: int = Query(500, ge=201, le=1000),
    horizon_bars: int = Query(8, ge=1, le=96),
    fee_bps: float = Query(10, ge=0, le=100, allow_inf_nan=False),
    slippage_bps: float = Query(5, ge=0, le=100, allow_inf_nan=False),
    min_volume_ratio: float = Query(1.5, ge=0, le=1000, allow_inf_nan=False),
):
    from app.services.signal_backtest import backtest

    try:
        result = backtest(
            db,
            symbol.upper(),
            now_ms(),
            history_limit,
            horizon_bars,
            fee_bps,
            slippage_bps,
            min_volume_ratio,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Backtest database unavailable") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Active Binance Spot USDT symbol not found")
    return result


@app.get("/analysis/binance/dashboard", include_in_schema=False)
def analysis_dashboard():
    from pathlib import Path

    from fastapi.responses import HTMLResponse

    page = Path(__file__).resolve().parent / "static" / "analysis_dashboard.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/analysis/binance/candidates")
def bullish_candidates(
    db: DbDep,
    candidate_limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    max_confirmation_age_bars: int = Query(4, ge=0, le=200),
    min_volume_ratio: float = Query(1.5, ge=0, le=1000, allow_inf_nan=False),
    min_quote_volume: float = Query(0, ge=0, allow_inf_nan=False),
    include_conflicting: bool = Query(False),
):
    from app.services.bullish_candidates import candidates

    try:
        return candidates(
            db,
            now_ms(),
            candidate_limit,
            offset,
            limit,
            max_confirmation_age_bars,
            min_volume_ratio,
            min_quote_volume,
            include_conflicting,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Candidate database unavailable") from exc


@app.get("/analysis/binance/similar")
def similar_coins(
    db: DbDep,
    symbol: str = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$"),
    lookback_bars: int = Query(48, ge=16, le=199),
    candidate_limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    min_quote_volume: float = Query(0, ge=0, allow_inf_nan=False),
    min_correlation: float = Query(0.3, ge=0, le=1, allow_inf_nan=False),
):
    from app.services.coin_similarity import similar

    try:
        result = similar(
            db,
            symbol.upper(),
            now_ms(),
            lookback_bars,
            candidate_limit,
            offset,
            limit,
            min_quote_volume,
            min_correlation,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Similarity database unavailable") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Active Binance Spot USDT symbol not found")
    return result


@app.get("/analysis/binance/history")
def formation_history(
    db: DbDep,
    symbol: str = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$"),
    pattern: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    from app.services.formation_history import history
    from app.services.formations import NAMES

    if pattern is not None and pattern not in NAMES:
        raise HTTPException(status_code=422, detail="Unknown formation pattern")
    try:
        result = history(db, symbol.upper(), pattern, limit, offset)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="History database unavailable") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Binance Spot USDT symbol not found")
    return result


@app.get("/analysis/binance/report")
def coin_analysis_report(
    db: DbDep,
    symbol: str = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$"),
    max_confirmation_age_bars: int = Query(4, ge=0, le=200),
    min_volume_ratio: float = Query(1.5, ge=0, le=1000, allow_inf_nan=False),
):
    from app.services.coin_report import build_report
    from app.services.formations import analyze

    try:
        result = analyze(db, symbol.upper(), now_ms())
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Analysis database unavailable") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Active Binance Spot USDT symbol not found")
    return build_report(result, max_confirmation_age_bars, min_volume_ratio)


@app.get("/analysis/binance/formations/scan")
def formation_scan(
    db: DbDep,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    direction: Literal["all", "up", "down"] = Query("all"),
    state: Literal["all", "forming", "confirmed", "invalidated"] = Query("confirmed"),
    min_quote_volume: float = Query(0, ge=0, allow_inf_nan=False),
    include_stablecoins: bool = Query(False),
    max_confirmation_age_bars: int | None = Query(None, ge=0, le=200),
    breakout_holding: bool | None = Query(None),
    min_volume_ratio: float | None = Query(None, ge=0, le=1000, allow_inf_nan=False),
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
            max_confirmation_age_bars,
            breakout_holding,
            min_volume_ratio,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Analysis database unavailable") from exc


@app.get("/analysis/binance/chart-data")
def formation_chart_data(
    db: DbDep,
    symbol: str = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$"),
):
    from app.services.formation_chart import chart_data

    try:
        result = chart_data(db, symbol.upper(), now_ms())
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Chart database unavailable") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Active Binance Spot USDT symbol not found")
    return result


@app.get("/analysis/binance/chart", include_in_schema=False)
def formation_chart_page():
    from pathlib import Path

    from fastapi.responses import HTMLResponse

    page = Path(__file__).resolve().parent / "static" / "formation_chart.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/analysis/binance/forward")
def forward_signals(
    db: DbDep,
    symbol: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    from app.services.forward_tracking import listing

    try:
        return listing(db, symbol.upper() if symbol else None, limit, offset)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Forward tracking database unavailable"
        ) from exc


@app.get("/analysis/binance/forward/summary")
def forward_summary(
    db: DbDep,
    days: int = Query(7, ge=1, le=30),
    rule_hash: str | None = Query(None, pattern="^[0-9a-f]{64}$"),
):
    from app.services.forward_summary import summarize

    try:
        return summarize(db, now_ms(), days, rule_hash)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Forward summary database unavailable") from exc


@app.post("/analysis/binance/forward/reports", status_code=201)
def create_forward_report(
    db: DbDep,
    days: int = Query(7, ge=1, le=30),
    request_id: UUID | None = None,
):
    from app.services.forward_reports import create_report

    try:
        return create_report(db, now_ms(), days, str(request_id) if request_id else None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Report database unavailable") from exc


@app.get("/analysis/binance/forward/reports")
def forward_reports(db: DbDep, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    from app.services.forward_reports import list_reports

    try:
        return list_reports(db, limit, offset)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Report database unavailable") from exc


@app.get("/analysis/binance/forward/reports/{report_id}")
def stored_forward_report(report_id: UUID, db: DbDep):
    from app.db.models.forward_report import ForwardReport

    try:
        row = db.get(ForwardReport, str(report_id))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Report database unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Saved report not found")
    return row.payload


@app.get("/analysis/binance/forward/reports/{report_id}/download")
def download_forward_report(report_id: UUID, db: DbDep):
    import json

    from fastapi.responses import Response

    payload = stored_forward_report(report_id, db)
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="forward_report_{report_id}.json"'},
    )
