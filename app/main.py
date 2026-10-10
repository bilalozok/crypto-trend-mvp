from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import app.db.models.account  # noqa: F401
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
def root():
    from starlette.responses import RedirectResponse

    return RedirectResponse("/analysis/binance/dashboard?tab=overview", status_code=303)


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


@app.get("/analysis/binance/indicators")
def coin_indicators(
    db: DbDep,
    symbol: str = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$"),
):
    from app.services.ichimoku_context import report

    try:
        result = report(db, symbol.upper(), now_ms())
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Indicator database unavailable") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Active Binance Spot USDT symbol not found")
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
    pattern: str | None = Query(None),
):
    from app.services.formation_scan import scan
    from app.services.formations import NAMES

    if pattern is not None and pattern not in NAMES:
        raise HTTPException(status_code=422, detail="Unknown formation pattern")

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
            pattern,
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


@app.get("/analysis/binance/forward/pattern-signals")
def forward_pattern_signals(
    db: DbDep,
    pattern: str = Query(min_length=1, max_length=100),
    hours: int = Query(1),
    days: int = Query(7, ge=1, le=30),
    rule_hash: str = Query(pattern="^[0-9a-f]{64}$"),
    as_of_ms: int = Query(ge=0),
    offset: int = Query(0, ge=0),
):
    from app.services.forward_pattern_signals import listing

    if hours not in (1, 2, 4):
        raise HTTPException(status_code=422, detail="Süre 1, 2 veya 4 saat olmalı.")
    if as_of_ms > now_ms():
        raise HTTPException(status_code=422, detail="Gelecekteki bir özet zamanı seçilemez.")
    try:
        return listing(db, as_of_ms, days, rule_hash, pattern, hours, offset)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Formasyon sinyalleri okunamadı.") from exc


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


# Every application route requires an active, revocable account session.
from app.api.account import router as account_router  # noqa: E402

app.include_router(account_router)


@app.get("/login", include_in_schema=False)
def login_page():
    from pathlib import Path

    from starlette.responses import FileResponse

    return FileResponse(Path(__file__).resolve().parent / "static" / "login.html")


@app.middleware("http")
async def application_access(request, call_next):
    from urllib.parse import quote

    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse, RedirectResponse

    from app.api.account import current

    public = (request.method, request.url.path) in {
        ("GET", "/login"),
        ("POST", "/account/login"),
        ("GET", "/health"),
    }
    if not public:

        def authenticate():
            with SessionLocal() as db:
                current(
                    db,
                    request,
                    mutate=request.method not in ("GET", "HEAD", "OPTIONS")
                    and not request.url.path.startswith("/account/"),
                )

        try:
            await run_in_threadpool(authenticate)
        except HTTPException as exc:
            pages = {
                "/",
                "/analysis/binance/dashboard",
                "/analysis/binance/chart",
                "/docs",
                "/redoc",
            }
            if (
                exc.status_code == 401
                and request.method in ("GET", "HEAD")
                and request.url.path in pages
            ):
                destination = request.url.path
                if request.url.query:
                    destination += "?" + request.url.query
                response = RedirectResponse(
                    "/login?next=" + quote(destination, safe=""), status_code=303
                )
            else:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            response.headers["Cache-Control"] = "no-store"
            return response
        except SQLAlchemyError:
            return JSONResponse(
                {"detail": "Oturum doğrulanamadı; daha sonra yeniden dene."},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/analysis/binance/early-formations")
def early_formations(db: DbDep):
    from app.services.formation_early import listing

    try:
        return listing(db, now_ms())
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Early formation data unavailable") from exc


@app.get("/analysis/binance/early-study")
def early_study(db: DbDep, days: int = Query(default=7, ge=1, le=30)):
    from app.services.early_measurement_details import report_with_details

    try:
        return report_with_details(db, now_ms(), days)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Erken uyarı ölçümleri hazır değil; migration ve worker ayarını kontrol et.",
        ) from exc


@app.get("/analysis/binance/early-study/records")
def early_study_records(
    db: DbDep,
    symbol: str = Query(default="", max_length=64),
    name: str = Query(default="", max_length=100),
    direction: Literal["all", "up", "down"] = "all",
    start: datetime | None = None,
    end: datetime | None = None,
    as_of: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0, le=100000),
):
    from app.services.early_measurement_search import search_records

    if any(value is not None and value.tzinfo is None for value in (start, end)):
        raise HTTPException(status_code=422, detail="Tarih saat dilimi içermeli.")
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="Başlangıç bitişten önce olmalı.")
    try:
        return search_records(
            db,
            now_ms(),
            symbol=symbol,
            name=name,
            direction=direction,
            start_ms=round(start.timestamp() * 1000) if start is not None else None,
            end_ms=round(end.timestamp() * 1000) if end is not None else None,
            as_of=as_of,
            offset=offset,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Erken ölçüm kayıtları şu anda okunamıyor."
        ) from exc


@app.get("/analysis/binance/early-study/record-options")
def early_study_record_options(db: DbDep):
    from app.services.early_measurement_search import record_options

    try:
        return record_options(db)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Coin ve formasyon seçenekleri şu anda okunamıyor."
        ) from exc
