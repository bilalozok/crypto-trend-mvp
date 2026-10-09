from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_formation_early import CLOSE, add

from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.early_formation import EarlyFormation
from app.db.session import SessionLocal
from app.services import early_formation_study as study
from app.services.formations import BAR


def row(open_time, price=100, close=101):
    return SimpleNamespace(open_time=open_time, open=price, close=close)


def test_measure_both_directions_and_missing_bars():
    rows = [row(i * BAR) for i in range(4)]
    assert study.measure(rows, 0, 1, "up")["price_change_pct"] == pytest.approx(1)
    assert study.measure(rows, 0, 1, "down")["directional_change_pct"] == pytest.approx(-1)
    assert study.measure(rows[:-1], 0, 1, "up")["status"] == "invalid_data"
    rows[-1].open_time += BAR
    assert study.measure(rows, 0, 1, "up")["status"] == "invalid_data"
    rows[-1].open_time -= BAR
    rows[0].open = float("nan")
    assert study.measure(rows, 0, 1, "up")["status"] == "invalid_data"


def test_live_snapshot_is_idempotent_no_backfill_and_settlement_after_due(monkeypatch):
    monkeypatch.setattr(study, "rules_hash", lambda: "a" * 64)
    with SessionLocal() as db:
        add(db, "BTCUSDT")
        for i in range(200):
            db.add(
                BinanceSpotCandle(
                    symbol="BTCUSDT",
                    open_time=i * BAR,
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=20,
                )
            )
        db.commit()
        assert study.track_symbol(db, "BTCUSDT", CLOSE + 1)["recorded"] == 1
        record = db.query(EarlyFormation).one()
        original = deepcopy(record.snapshot)
        assert record.entry_ms >= record.observed_ms + BAR
        assert record.snapshot["indicator_status"] == "ready"
        assert study.track_symbol(db, "BTCUSDT", CLOSE + 1000)["recorded"] == 0
        assert study.track_symbol(db, "BTCUSDT", CLOSE + BAR)["recorded"] == 0
        for i in range(4):
            db.add(
                BinanceSpotCandle(
                    symbol="BTCUSDT",
                    open_time=record.entry_ms + i * BAR,
                    open=100,
                    high=102,
                    low=99,
                    close=101,
                    volume=20,
                )
            )
        db.commit()
        assert study.settle_symbol(db, "BTCUSDT", record.entry_ms + 4 * BAR - 1) == 0
        assert study.settle_symbol(db, "BTCUSDT", record.entry_ms + 4 * BAR) == 1
        db.commit()
        assert record.outcomes["1"]["status"] == "complete"
        assert "2" not in record.outcomes
        assert record.snapshot == original
        assert study.settle_symbol(db, "BTCUSDT", record.entry_ms + 4 * BAR) == 0


def test_overlap_is_outcome_independent_and_versions_and_directions_separate():
    def record(symbol, entry, direction="up", rule="r", done=True):
        return SimpleNamespace(
            symbol=symbol,
            entry_ms=entry,
            observed_ms=entry - 1,
            pattern="dip",
            rule_hash=rule,
            snapshot=dict(
                name="Dip",
                direction=direction,
                confluence=dict(
                    patterns=[dict(groups=[dict(name="Trend · EMA50", state="supports")])]
                ),
            ),
            outcomes=(
                {
                    str(h): dict(
                        status="complete",
                        price_change_pct=2,
                        directional_change_pct=2 if direction == "up" else -2,
                    )
                    for h in study.HOURS
                }
                if done
                else {}
            ),
        )

    result = study.comparison(
        [
            record("A", 100, done=False),
            record("A", 101),
            record("B", 100, direction="down"),
            record("A", 101, rule="other"),
        ]
    )
    assert result["overlap_excluded"] == 1
    assert result["retained"] == 3
    group = next(g for g in result["groups"] if g["rule_hash"] == "r" and g["direction"] == "up")
    assert group["paired"] == 0 and group["mean_price_change_pct"] is None
    down = next(g for g in result["groups"] if g["direction"] == "down")
    assert down["mean_price_change_pct"] == 2
    assert down["mean_directional_change_pct"] == -2


def test_measurement_endpoint_is_read_only_and_requires_login(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "now_ms", lambda: CLOSE)
    assert client.get("/analysis/binance/early-study").status_code == 200
    assert client.get("/analysis/binance/early-study?days=31").status_code == 422
    client.cookies.clear()
    assert client.get("/analysis/binance/early-study").status_code == 401
