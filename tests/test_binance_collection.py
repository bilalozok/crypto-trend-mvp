from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.candle import Candle
from app.db.session import SessionLocal
from app.services.binance_collection import BAR_MS, refresh_symbol, sync_symbols
from app.services.binance_coverage import coverage
from app.services.binance_market import BinanceMarketError
from app.workers.market_worker import collect_market

NOW = 1_700_002_800_000


def _catalog(*symbols):
    return [
        SimpleNamespace(symbol=s, base_asset=s.removesuffix("USDT"), quote_volume_24h=1000)
        for s in symbols
    ]


def _candle(opened, close=105):
    return [opened, "100", "110", "90", str(close), "1000", opened + BAR_MS - 1]


def _setup(*symbols):
    with SessionLocal() as db:
        sync_symbols(db, _catalog(*symbols), NOW)


def test_catalog_sync_preserves_history_and_deactivates_removed_pairs():
    _setup("BTCUSDT", "ETHUSDT")
    with SessionLocal() as db:
        row = db.get(BinanceSpotSymbol, "BTCUSDT")
        row.last_success_ms = NOW
        db.commit()
        sync_symbols(db, _catalog("BTCUSDT"), NOW + 1)
        assert db.get(BinanceSpotSymbol, "BTCUSDT").last_success_ms == NOW
        assert db.get(BinanceSpotSymbol, "ETHUSDT").active is False
        with pytest.raises(ValueError):
            sync_symbols(db, [], NOW)
        assert db.get(BinanceSpotSymbol, "BTCUSDT").active is True


def test_collection_isolated_closed_and_idempotent(monkeypatch):
    _setup("BTCUSDT")
    payload = [_candle(NOW - BAR_MS), _candle(NOW)]
    calls = []
    monkeypatch.setattr("app.services.binance_collection.now_ms", lambda: NOW)
    monkeypatch.setattr(
        "app.services.binance_collection._get",
        lambda path, params: calls.append(params.copy()) or payload,
    )
    with SessionLocal() as db:
        assert refresh_symbol(db, "BTCUSDT") == 1
        payload[0][4] = "108"
        assert refresh_symbol(db, "BTCUSDT") == 1
        rows = db.scalars(select(BinanceSpotCandle)).all()
        assert len(rows) == 1 and rows[0].close == 108
        assert db.query(Candle).count() == 0
        assert calls[0] == {"symbol": "BTCUSDT", "interval": "15m", "limit": 500}
        assert calls[1]["startTime"] == NOW - 2 * BAR_MS
        assert db.get(BinanceSpotSymbol, "BTCUSDT").last_success_ms == NOW


def test_invalid_batch_does_not_partially_store(monkeypatch):
    _setup("BTCUSDT")
    monkeypatch.setattr("app.services.binance_collection.now_ms", lambda: NOW)
    monkeypatch.setattr(
        "app.services.binance_collection._get",
        lambda *args: [_candle(NOW - 2 * BAR_MS), _candle(NOW - BAR_MS, close=float("nan"))],
    )
    with SessionLocal() as db:
        with pytest.raises(BinanceMarketError):
            refresh_symbol(db, "BTCUSDT")
        assert db.query(BinanceSpotCandle).count() == 0
        assert db.get(BinanceSpotSymbol, "BTCUSDT").last_success_ms is None


def test_coverage_reports_staleness_gaps_and_stablecoins(client, monkeypatch):
    _setup("BTCUSDT", "USDCUSDT")
    with SessionLocal() as db:
        db.add_all(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                open_time=t,
                interval="15m",
                open=100,
                high=110,
                low=90,
                close=105,
                volume=1000,
            )
            for t in [NOW - 3 * BAR_MS, NOW - BAR_MS]
        )
        db.commit()
        body = coverage(db, 10, 0, 3, NOW)
        btc = next(r for r in body["symbols"] if r["symbol"] == "BTCUSDT")
        assert btc["candles_count"] == 2
        assert btc["history_sufficient"] is False
        assert btc["missing_candles_in_stored_range"] == 1
        assert btc["stale"] is False
        assert coverage(db, 10, 0, 2, NOW + BAR_MS)["symbols"][0]["stale"] is True
        stable = next(r for r in body["symbols"] if r["symbol"] == "USDCUSDT")
        assert stable["stablecoin_candidate"] is True
        assert stable["stale"] is True
    monkeypatch.setattr("app.main.now_ms", lambda: NOW)
    response = client.get("/market/binance/coverage?limit=1")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    assert len(response.json()["symbols"]) == 1
    response = client.get("/market/binance/candles?symbol=BTCUSDT")
    assert response.status_code == 200, response.text
    assert response.json()["stored"] is True
    assert len(response.json()["candles"]) == 2


def test_market_worker_rotates_deferred_pairs(monkeypatch):
    monkeypatch.setattr(
        "app.workers.market_worker.catalog.snapshot",
        lambda: (datetime.now(UTC), _catalog("BTCUSDT", "ETHUSDT", "SOLUSDT")),
    )
    calls = []

    def refresh(db, symbol, limit):
        calls.append(symbol)
        item = db.get(BinanceSpotSymbol, symbol)
        item.last_attempt_ms = NOW
        db.commit()
        return 1

    ticks = iter([0, 0, 181, 181])
    monkeypatch.setattr("app.workers.market_worker.monotonic", lambda: next(ticks, 181))
    monkeypatch.setattr("app.services.binance_collection.refresh_symbol", refresh)
    assert collect_market(workers=1) == 0
    assert calls == ["BTCUSDT"]
    monkeypatch.setattr("app.workers.market_worker.monotonic", lambda: 0)
    assert collect_market(workers=1) == 0
    assert calls[1:] == ["ETHUSDT", "SOLUSDT", "BTCUSDT"]


def test_market_worker_stops_on_rate_limit(monkeypatch):
    monkeypatch.setattr(
        "app.workers.market_worker.catalog.snapshot",
        lambda: (datetime.now(UTC), _catalog("BTCUSDT", "ETHUSDT")),
    )
    calls = []

    def denied(db, symbol, limit):
        calls.append(symbol)
        raise BinanceMarketError("Rate limit", 503)

    monkeypatch.setattr("app.services.binance_collection.refresh_symbol", denied)
    assert collect_market(workers=1) == 1
    assert calls == ["BTCUSDT"]
    with SessionLocal() as db:
        assert db.get(BinanceSpotSymbol, "BTCUSDT").last_error == "BinanceMarketError"


def test_migration_upgrade_preserves_legacy_candles(tmp_path, monkeypatch):
    import importlib

    command = importlib.import_module("alembic.command")
    Config = importlib.import_module("alembic.config").Config

    url = "sqlite:///" + str(tmp_path / "migration.db")
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "4125d212c4aa")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO candles (id, symbol, interval, open_time, open, high, low, close, volume) "
            "VALUES (1, 'BTCUSDT', '1h', 1, 1, 1, 1, 1, 1)"
        )
    command.upgrade(cfg, "head")
    assert {"binance_spot_symbols", "binance_spot_candles", "early_formation_measurements"} <= set(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM candles").scalar() == 1
        version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
        assert version == "0910e2026l01"
    engine.dispose()


@pytest.mark.parametrize(
    "failure,expected",
    [("market", 0), ("all_market", 1), ("internal", 1), ("blocked", 1), ("history", 1)],
)
def test_worker_partial_market_failure_preserves_fatal_errors(
    monkeypatch, caplog, failure, expected
):
    monkeypatch.setattr(
        "app.workers.market_worker.catalog.snapshot",
        lambda: (datetime.now(UTC), _catalog("BTCUSDT", "ETHUSDT")),
    )
    for key in (
        "PORTFOLIO_TIMEFRAMES_ENABLED",
        "FORWARD_TRACKING_ENABLED",
        "PORTFOLIO_DAILY_ENABLED",
        "CANDIDATE_OBSERVATIONS_ENABLED",
        "CANDIDATE_OUTCOMES_ENABLED",
    ):
        monkeypatch.setenv(key, "false")

    def refresh(db, symbol, limit):
        if failure == "all_market" or symbol == "BTCUSDT" and failure != "history":
            if failure == "internal":
                raise RuntimeError("private-secret-detail")
            raise BinanceMarketError(
                "Binance returned no candles", 503 if failure == "blocked" else 502
            )
        return 1

    def history(db, symbol, stamp):
        if failure == "history" and symbol == "BTCUSDT":
            raise RuntimeError("private-secret-detail")
        return 0

    monkeypatch.setattr("app.services.binance_collection.refresh_symbol", refresh)
    monkeypatch.setattr("app.services.formation_history.record_symbol", history)
    with caplog.at_level("INFO"):
        assert collect_market(workers=1) == expected
    assert "private-secret-detail" not in caplog.text
    assert ("outcome=partial" if expected == 0 else "outcome=failed") in caplog.text
    if failure == "market":
        assert "reason=empty_candles" in caplog.text
        with SessionLocal() as db:
            assert db.get(BinanceSpotSymbol, "BTCUSDT").last_error == "BinanceMarketError"
        retried = []
        monkeypatch.setattr(
            "app.services.binance_collection.refresh_symbol",
            lambda db, symbol, limit: retried.append(symbol) or 1,
        )
        assert collect_market(workers=1) == 0
        assert set(retried) == {"BTCUSDT", "ETHUSDT"}


def test_worker_logs_only_safe_http_error_metadata():
    import requests

    from app.workers.market_worker import market_error_details

    response = requests.Response()
    response.status_code = 400
    cause = requests.HTTPError("secret URL and response content", response=response)
    error = BinanceMarketError("Binance market data request failed")
    error.__cause__ = cause
    assert market_error_details(error) == ("request_failed", 400)
    assert market_error_details(RuntimeError("secret")) == ("internal_error", None)
