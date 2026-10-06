from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest
from test_private_purchases import STAMP, auth, record
from test_private_purchases import private_client as private_setup

from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.portfolio_timeframe import PortfolioCandle, PortfolioFeed
from app.db.session import SessionLocal
from app.services import formations
from app.services import portfolio_feeds as feeds
from app.services import portfolio_timeframes as engine
from app.services.binance_market import BinanceMarketError
from tests.test_head_shoulders import structure


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def real_rows(interval, up=False):
    bar = engine.INTERVALS[interval]
    end = STAMP // bar * bar
    return [
        SimpleNamespace(**{**vars(r), "open_time": end - 200 * bar + i * bar})
        for i, r in enumerate(structure(up=up))
    ]


@pytest.mark.parametrize("interval", ["4h", "1d"])
@pytest.mark.parametrize("up", [False, True])
def test_real_engine_preserves_geometry_restores_every_timestamp(interval, up):
    source = real_rows(interval, up)
    reference = formations.analyze_rows(structure(up=up), "BTCUSDT", 200 * formations.BAR)
    actual = engine.analyze_rows(source, "BTCUSDT", STAMP, interval)
    bar = engine.INTERVALS[interval]
    base = source[0].open_time

    def transform(item):
        if isinstance(item, datetime):
            return formations.timestamp(
                base + round(item.timestamp() * 1000 * bar / formations.BAR)
            )
        if isinstance(item, dict):
            return {k: transform(v) for k, v in item.items()}
        if isinstance(item, list):
            return [transform(v) for v in item]
        return item

    assert actual["status"] == "ready"
    assert actual["patterns"] == transform(reference["patterns"])
    key = "inverse_head_and_shoulders" if up else "head_and_shoulders"
    p = next(p for p in actual["patterns"] if p["pattern"] == key)
    assert p["status"] == "confirmed"
    assert p["structure_available_at"] <= p["confirmed_at"] <= formations.timestamp(STAMP)
    assert (
        p["confirmation_age_bars"]
        == next(p for p in reference["patterns"] if p["pattern"] == key)["confirmation_age_bars"]
    )


@pytest.mark.parametrize(
    "damage,status",
    [
        ("gap", "missing_data"),
        ("old", "stale_data"),
        ("unaligned", "invalid_data"),
        ("open", "invalid_data"),
        ("short", "insufficient_data"),
    ],
)
def test_invalid_source_never_becomes_ready(damage, status):
    rows = real_rows("4h")
    bar = engine.INTERVALS["4h"]
    if damage == "gap":
        rows[50].open_time += bar
    if damage == "old":
        for row in rows:
            row.open_time -= bar
    if damage == "unaligned":
        rows[50].open_time += 1
    if damage == "open":
        rows[-1].open_time = STAMP // bar * bar
    if damage == "short":
        rows = rows[1:]
    result = engine.analyze_rows(rows, "BTCUSDT", STAMP, "4h")
    assert result["status"] == status and result["patterns"] == []


def test_parallel_timeframes_do_not_modify_global_engine():
    before = formations.analyze_rows(structure(), "BTCUSDT", 200 * formations.BAR)

    def evaluate(interval):
        return engine.analyze_rows(real_rows(interval), "BTCUSDT", STAMP, interval)

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(evaluate, ["4h", "1d"]))
    assert all(r["status"] == "ready" for r in result)
    assert formations.BAR == 900000
    assert formations.analyze_rows(structure(), "BTCUSDT", 200 * formations.BAR) == before


def candle(opened, bar, price=10):
    return [opened, "10", "11", "9", str(price), "1", opened + bar - 1]


def test_feed_closed_only_cached_and_other_intervals_untouched(private_client, monkeypatch):
    headers = auth(private_client)
    assert (
        private_client.post("/account/purchases", json=record(), headers=headers).status_code == 201
    )
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        bar = engine.INTERVALS[kwargs["params"]["interval"]]
        end = STAMP // bar * bar
        return SimpleNamespace(
            status_code=200,
            json=lambda: [candle(end - 200 * bar + i * bar, bar) for i in range(201)],
        )

    monkeypatch.setattr(feeds.requests, "get", get)
    assert (
        private_client.post("/account/portfolio/refresh", json={"symbol": "BTCUSDT"}).status_code
        == 403
    )
    first = private_client.post(
        "/account/portfolio/refresh", json={"symbol": "BTCUSDT"}, headers=headers
    )
    assert first.status_code == 200
    assert first.json()["feeds"]["1d"]["candles"] == 200
    second = private_client.post(
        "/account/portfolio/refresh", json={"symbol": "BTCUSDT"}, headers=headers
    )
    assert second.json()["feeds"]["4h"]["status"] == "cached" and len(calls) == 2
    with SessionLocal() as db:
        assert db.query(PortfolioCandle).count() == 400
        assert db.query(BinanceSpotCandle).count() == 0
    data = private_client.get("/account/portfolio").json()["coins"][0]
    assert [h["status"] for h in data["horizons"]] == ["insufficient_data", "ready", "ready"]
    auth(private_client, "bob")
    headers = {"X-CSRF-Token": private_client.get("/account/session").json()["csrf_token"]}
    assert (
        private_client.post(
            "/account/portfolio/refresh", json={"symbol": "BTCUSDT"}, headers=headers
        ).status_code
        == 404
    )
    assert len(calls) == 2


@pytest.mark.parametrize("damage", ["gap", "nan", "wrong_close", "duplicate", "stale"])
def test_feed_invalid_batch_is_atomic_and_throttled(private_client, monkeypatch, damage):
    auth(private_client)
    bar = engine.INTERVALS["4h"]
    end = STAMP // bar * bar
    payload = [candle(end - 2 * bar, bar), candle(end - bar, bar)]
    if damage == "gap":
        payload[0] = candle(end - 3 * bar, bar)
    if damage == "nan":
        payload[0][4] = "NaN"
    if damage == "wrong_close":
        payload[0][6] += 1
    if damage == "duplicate":
        payload = [payload[-1], payload[-1]]
    if damage == "stale":
        payload = payload[:1]
    monkeypatch.setattr(
        feeds.requests,
        "get",
        lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: payload),
    )
    with SessionLocal() as db:
        with pytest.raises(BinanceMarketError):
            feeds.refresh(db, "BTCUSDT", "4h", STAMP)
        assert db.query(PortfolioCandle).count() == 0
        assert db.get(PortfolioFeed, ("BTCUSDT", "4h")).last_success_ms is None
        assert feeds.refresh(db, "BTCUSDT", "4h", STAMP)["status"] == "throttled"


def test_worker_collects_only_owned_symbols_when_enabled(private_client, monkeypatch):
    from datetime import UTC

    from app.schemas.market import SpotSymbolOut
    from app.workers.market_worker import collect_market

    headers = auth(private_client)
    private_client.post("/account/purchases", json=record(), headers=headers)
    monkeypatch.setenv("PORTFOLIO_TIMEFRAMES_ENABLED", "true")
    monkeypatch.setattr(
        "app.workers.market_worker.catalog.snapshot",
        lambda: (
            datetime.now(UTC),
            [SpotSymbolOut(symbol="BTCUSDT", base_asset="BTC", quote_volume_24h=1)],
        ),
    )
    monkeypatch.setattr("app.services.binance_collection.refresh_symbol", lambda *a: 0)
    monkeypatch.setattr("app.services.formation_history.record_symbol", lambda *a: 0)
    calls = []
    monkeypatch.setattr(feeds, "refresh_symbol", lambda db, symbol, stamp: calls.append(symbol))
    assert collect_market(workers=1) == 0
    assert calls == ["BTCUSDT"]


def test_daily_comparison_includes_medium_change(private_client):
    from uuid import uuid4

    from app.db.models.account import Account
    from app.db.models.portfolio_observation import PortfolioObservation
    from app.services.portfolio_technical import VERSION, technical

    headers = auth(private_client)
    private_client.post("/account/purchases", json=record(), headers=headers)
    with SessionLocal() as db:
        for i, r in enumerate(structure()):
            db.add(
                BinanceSpotCandle(
                    symbol="BTCUSDT",
                    interval="15m",
                    open_time=STAMP - 200 * formations.BAR + i * formations.BAR,
                    open=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    volume=r.volume,
                )
            )
        for r in real_rows("4h"):
            db.add(PortfolioCandle(symbol="BTCUSDT", interval="4h", **vars(r)))
        db.commit()
        payload = technical(db, "BTCUSDT", STAMP)
        payload["horizons"][1].update(assessment="waiting", label="Teyit bekleniyor")
        owner = db.query(Account).filter_by(username="alice").first().id
        db.add(
            PortfolioObservation(
                id=str(uuid4()),
                account_id=owner,
                symbol="BTCUSDT",
                local_day="2026-10-04",
                observed_ms=STAMP - 86400000,
                method=VERSION,
                payload=payload,
            )
        )
        db.commit()
    data = private_client.get("/account/portfolio").json()["coins"][0]
    assert "Orta: Teyit bekleniyor → Düşüş baskısı" in data["daily_change"]
    assert "Uzun: veri karşılaştırılamıyor" in data["daily_change"]
