from datetime import UTC, datetime

from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.formation_history import FormationEvent, FormationState
from app.db.session import SessionLocal
from app.services.formation_history import record_symbol, save_analysis
from app.services.formations import BAR, timestamp


def analysis(bar=200, status="forming", anchor=190, quality="ready"):
    return dict(
        symbol="BTCUSDT",
        status=quality,
        method_version="price_patterns_v1",
        patterns=[
            dict(
                pattern="double_bottom",
                name="Çift dip",
                status=status,
                direction="up",
                start_time=timestamp(160 * BAR),
                anchor_time=timestamp(anchor * BAR),
                confirmed_at=timestamp(bar * BAR) if status == "confirmed" else None,
                breakout_holding=status == "confirmed",
                last_candle_close_time=timestamp(bar * BAR),
            )
        ],
    )


def test_initial_repeat_and_same_bar_revision_are_idempotent():
    with SessionLocal() as db:
        assert save_analysis(db, analysis(), 200 * BAR + 1) == 1
        db.commit()
        assert save_analysis(db, analysis(status="confirmed"), 200 * BAR + 2) == 0
        db.commit()
        assert db.query(FormationEvent).count() == 1
        assert db.query(FormationState).one().signature["status"] == "forming"
        assert db.query(FormationEvent).one().event_type == "initial_observation"


def test_transitions_and_disappearance():
    with SessionLocal() as db:
        for bar, status in [
            (200, "forming"),
            (201, "confirmed"),
            (202, "invalidated"),
            (203, "not_detected"),
        ]:
            assert save_analysis(db, analysis(bar, status), bar * BAR) == 1
            db.commit()
        events = db.query(FormationEvent).order_by(FormationEvent.candle_close_ms).all()
        assert [e.event_type for e in events] == [
            "initial_observation",
            "changed",
            "changed",
            "disappeared",
        ]
        assert events[1].previous["status"] == "forming"


def test_absent_initial_state_then_appearance_and_new_structure():
    with SessionLocal() as db:
        assert save_analysis(db, analysis(status="not_detected"), 200 * BAR) == 0
        db.commit()
        assert save_analysis(db, analysis(201), 201 * BAR) == 1
        db.commit()
        assert save_analysis(db, analysis(202, anchor=191), 202 * BAR) == 1
        db.commit()
        assert {e.event_type for e in db.query(FormationEvent)} == {"appeared", "new_structure"}


def test_unchanged_skipped_and_stale_observations_do_not_create_history():
    with SessionLocal() as db:
        save_analysis(db, analysis(), 200 * BAR)
        db.commit()
        assert save_analysis(db, analysis(205), 205 * BAR) == 0
        db.commit()
        assert save_analysis(db, analysis(206, "confirmed", quality="stale_data"), 206 * BAR) == 0
        assert save_analysis(db, analysis(201, "confirmed"), 207 * BAR) == 0
        db.commit()
        assert db.query(FormationEvent).count() == 1
        assert db.query(FormationState).one().candle_close_ms == 205 * BAR


def test_record_and_read_endpoint(client, monkeypatch):
    monkeypatch.setattr("app.services.formation_history.analyze", lambda *args: analysis())
    with SessionLocal() as db:
        db.add(
            BinanceSpotSymbol(
                symbol="BTCUSDT",
                base_asset="BTC",
                active=True,
                quote_volume_24h=1,
                catalog_updated_ms=0,
            )
        )
        db.commit()
        assert record_symbol(db, "BTCUSDT", 200 * BAR) == 1
        assert record_symbol(db, "BTCUSDT", 200 * BAR + 1) == 0
    response = client.get("/analysis/binance/history?symbol=btcusdt&limit=1")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["events"][0]["event_type"] == "initial_observation"
    assert client.get("/analysis/binance/history?symbol=UNKNOWNUSDT").status_code == 404
    assert client.get("/analysis/binance/history?symbol=BTCUSDT&pattern=bad").status_code == 422
    assert (
        client.get("/analysis/binance/history?symbol=BTCUSDT&pattern=double_top").json()["total"]
        == 0
    )
    assert client.get("/analysis/binance/history?symbol=BTCUSDT&offset=1").json()["events"] == []


def test_market_worker_calls_history_after_refresh(monkeypatch):
    from app.schemas.market import SpotSymbolOut
    from app.workers.market_worker import collect_market

    monkeypatch.setattr(
        "app.workers.market_worker.catalog.snapshot",
        lambda: (
            datetime.now(UTC),
            [SpotSymbolOut(symbol="BTCUSDT", base_asset="BTC", quote_volume_24h=1)],
        ),
    )
    called = []
    monkeypatch.setattr(
        "app.services.binance_collection.refresh_symbol", lambda *args: called.append("fetch") or 1
    )
    monkeypatch.setattr(
        "app.services.formation_history.record_symbol", lambda *args: called.append("history") or 0
    )
    assert collect_market(workers=1) == 0
    assert called == ["fetch", "history"]


def test_history_migration_preserves_binance_candles(tmp_path, monkeypatch):
    import importlib

    from sqlalchemy import create_engine, inspect

    command = importlib.import_module("alembic.command")
    Config = importlib.import_module("alembic.config").Config
    url = "sqlite:///" + str(tmp_path / "history.db")
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "7b15c2026a01")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO binance_spot_candles "
                "(symbol, open_time, interval, open, high, low, close, volume) "
                "VALUES ('BTCUSDT', 0, '15m', 100, 101, 99, 100, 10)"
            )
        command.upgrade(cfg, "head")
        assert {"binance_formation_states", "binance_formation_events"} <= set(
            inspect(engine).get_table_names()
        )
        command.downgrade(cfg, "7b15c2026a01")
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT count(*) FROM binance_spot_candles").scalar()
                == 1
            )
    finally:
        engine.dispose()
