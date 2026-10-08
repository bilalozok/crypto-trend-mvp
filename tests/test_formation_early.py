from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.formation_history import FormationEvent, FormationState
from app.db.session import SessionLocal
from app.services.formation_early import listing
from app.services.formations import BAR, timestamp

CLOSE = 200 * BAR


def add(db, symbol, direction="up", available=CLOSE, observed=CLOSE, status="forming", active=True):
    current = dict(
        status=status,
        direction=direction,
        start_time=timestamp(CLOSE - 10 * BAR).isoformat(),
        anchor_time=timestamp(CLOSE - 4 * BAR).isoformat(),
        structure_available_at=timestamp(available).isoformat(),
        name="Çift dip",
    )
    db.add(
        BinanceSpotSymbol(
            symbol=symbol,
            base_asset=symbol[:-4],
            active=active,
            quote_volume_24h=100,
            catalog_updated_ms=CLOSE,
        )
    )
    db.add(
        FormationState(
            symbol=symbol, pattern="double_bottom", candle_close_ms=CLOSE, signature=current.copy()
        )
    )
    db.add(
        FormationEvent(
            symbol=symbol,
            pattern="double_bottom",
            candle_close_ms=CLOSE,
            observed_ms=observed,
            event_type="initial_observation",
            current=current,
            method_version="price_patterns_v1",
        )
    )


def test_fresh_up_down_and_strict_one_candle_window():
    with SessionLocal() as db:
        add(db, "BTCUSDT")
        add(db, "ETHUSDT", direction="down")
        add(db, "OLDUSDT", available=CLOSE - BAR)
        add(db, "FUTUREUSDT", observed=CLOSE + 1000)
        add(db, "DONEUSDT", status="confirmed")
        add(db, "OFFUSDT", active=False)
        add(db, "USDCUSDT")
        db.commit()
        assert {a["symbol"] for a in listing(db, CLOSE)["alerts"]} == {"BTCUSDT", "ETHUSDT"}
        assert len(listing(db, CLOSE + BAR - 1)["alerts"]) == 3
        assert listing(db, CLOSE + BAR)["alerts"] == []
        assert db.query(FormationEvent).count() == 7


def test_current_structure_must_match_and_future_candle_excluded(client, monkeypatch):
    with SessionLocal() as db:
        add(db, "BTCUSDT")
        db.flush()
        state = db.get(FormationState, ("BTCUSDT", "double_bottom"))
        state.signature = {**state.signature, "status": "invalidated"}
        db.commit()
        assert listing(db, CLOSE)["alerts"] == []
        assert listing(db, CLOSE - 1)["alerts"] == []
    monkeypatch.setattr("app.main.now_ms", lambda: CLOSE)
    response = client.get("/analysis/binance/early-formations")
    assert response.status_code == 200
    assert response.json()["alerts"] == []
