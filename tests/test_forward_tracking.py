import pytest

from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.forward_signal import ForwardSignal
from app.db.session import SessionLocal
from app.services.formations import BAR, timestamp
from app.services.forward_tracking import listing, save_observation, settle_pending, track_symbol
from tests.test_formation_scan import seed


def analysis(closed=200 * BAR, direction="up"):
    p = dict(
        pattern="double_bottom",
        name="Çift dip",
        status="confirmed",
        direction=direction,
        confirmation_age_bars=0,
        breakout_holding=True,
        volume_ratio=3,
        distance_from_breakout_pct=0.5,
        breakout_level=100,
        confirmed_at=timestamp(closed),
        last_candle_close_time=timestamp(closed),
    )
    return dict(symbol="BTCUSDT", status="ready", method_version="price_patterns_v1", patterns=[p])


def test_snapshot_is_immutable_and_entry_is_after_observation(monkeypatch):
    monkeypatch.setattr("app.services.forward_tracking.analyze", lambda *args: analysis())
    with SessionLocal() as db:
        seed(db, "BTCUSDT", count=0)
        assert track_symbol(db, "BTCUSDT", 200 * BAR + 60000)["recorded"] == 1
        row = db.query(ForwardSignal).one()
        snapshot = dict(row.snapshot)
        assert row.entry_ms == 201 * BAR
        assert row.observed_ms < row.entry_ms
        changed = analysis()
        changed["patterns"][0]["volume_ratio"] = 100
        monkeypatch.setattr("app.services.forward_tracking.analyze", lambda *args: changed)
        assert track_symbol(db, "BTCUSDT", 200 * BAR + 120000)["recorded"] == 0
        assert db.query(ForwardSignal).one().snapshot == snapshot
        assert listing(db)["signals"][0]["observation_delay_seconds"] == 60


def test_cooldown_and_old_confirmations_are_not_recorded():
    with SessionLocal() as db:
        assert save_observation(db, analysis(), 200 * BAR + 1, "rules") == 1
        db.commit()
        assert save_observation(db, analysis(201 * BAR), 201 * BAR + 1, "rules") == 0
        assert save_observation(db, analysis(), 216 * BAR + 1, "rules") == 0
        assert save_observation(db, analysis(216 * BAR), 216 * BAR + 1, "rules") == 1
        db.commit()
        assert db.query(ForwardSignal).count() == 2


def test_results_wait_for_full_closed_horizon_and_do_not_rewrite():
    with SessionLocal() as db:
        save_observation(db, analysis(), 200 * BAR + 1, "rules")
        db.commit()
        for i in range(201, 217):
            db.add(
                BinanceSpotCandle(
                    symbol="BTCUSDT",
                    interval="15m",
                    open_time=i * BAR,
                    open=100,
                    high=111,
                    low=99,
                    close=110,
                    volume=1,
                )
            )
        db.commit()
        assert settle_pending(db, "BTCUSDT", 205 * BAR - 1) == 0
        assert settle_pending(db, "BTCUSDT", 205 * BAR) == 1
        db.commit()
        row = db.query(ForwardSignal).one()
        original = dict(row.outcomes)
        assert original["4"]["status"] == "complete"
        assert original["4"]["result"]["net_return_pct"] < 10
        assert settle_pending(db, "BTCUSDT", 217 * BAR) == 2
        db.commit()
        assert db.query(ForwardSignal).one().outcomes["4"] == original["4"]
        assert db.query(ForwardSignal).one().complete is True


def test_missing_forward_candles_are_invalid_not_returns():
    with SessionLocal() as db:
        save_observation(db, analysis(), 200 * BAR + 1, "rules")
        db.commit()
        assert settle_pending(db, "BTCUSDT", 217 * BAR) == 3
        db.commit()
        outcomes = db.query(ForwardSignal).one().outcomes
        assert all(
            item["status"] == "invalid_data" and item["result"] is None
            for item in outcomes.values()
        )


def test_failure_rolls_back_snapshot(monkeypatch):
    from app.services import forward_tracking

    monkeypatch.setattr(forward_tracking, "analyze", lambda *args: analysis())
    original = forward_tracking.save_observation

    def fail(db, *args):
        original(db, *args)
        db.flush()
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(forward_tracking, "save_observation", fail)
    with SessionLocal() as db:
        seed(db, "BTCUSDT", count=0)
        with pytest.raises(RuntimeError):
            track_symbol(db, "BTCUSDT", 200 * BAR + 1)
        assert db.query(ForwardSignal).count() == 0


def test_api_and_stablecoin_exclusion(client, monkeypatch):
    monkeypatch.setattr("app.services.forward_tracking.analyze", lambda *args: analysis())
    with SessionLocal() as db:
        seed(db, "USD1USDT", base="USD1", count=0)
        assert track_symbol(db, "USD1USDT", 200 * BAR + 1)["recorded"] == 0
    assert client.get("/analysis/binance/forward").json()["total"] == 0
    assert client.get("/analysis/binance/forward?limit=0").status_code == 422


def test_real_detector_records_current_confirmation():
    from tests.test_backtest_causality import confirmed_history

    with SessionLocal() as db:
        seed(db, "BTCUSDT", count=0)
        db.add_all(
            [
                BinanceSpotCandle(symbol="BTCUSDT", interval="15m", **vars(row))
                for row in confirmed_history()
            ]
        )
        db.commit()
        assert track_symbol(db, "BTCUSDT", 200 * BAR + 60000)["recorded"] == 1
        row = db.query(ForwardSignal).one()
        assert row.snapshot["primary_pattern"]["pattern"] == "double_bottom"
        assert row.entry_ms == 201 * BAR


@pytest.mark.parametrize("enabled,expected", [("false", 0), ("true", 1)])
def test_worker_records_only_when_enabled(monkeypatch, enabled, expected):
    from datetime import UTC, datetime

    from app.workers.market_worker import collect_market
    from tests.test_binance_collection import _catalog

    calls = []
    monkeypatch.setenv("FORWARD_TRACKING_ENABLED", enabled)
    monkeypatch.setattr(
        "app.workers.market_worker.catalog.snapshot",
        lambda: (datetime.now(UTC), _catalog("BTCUSDT")),
    )
    monkeypatch.setattr("app.services.binance_collection.refresh_symbol", lambda *args: 1)
    monkeypatch.setattr("app.services.formation_history.record_symbol", lambda *args: 0)

    def tracked(db, symbol, stamp):
        calls.append(symbol)
        return dict(recorded=1, settled=0)

    monkeypatch.setattr("app.services.forward_tracking.track_symbol", tracked)
    assert collect_market(workers=1) == 0
    assert len(calls) == expected
