from types import SimpleNamespace

import pytest

from app.services.formations import BAR, detect
from app.services.triple_patterns import detect_triple


def triple(up=True, ending=104, third=90):
    values = [100.0] * 200
    points = [
        (150, 100),
        (160, 90),
        (166, 100),
        (174, 90),
        (181, 101),
        (188, third),
        (193, 97),
        (199, ending),
    ]
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    if not up:
        values = [200 - v for v in values]
    rows = [
        SimpleNamespace(open_time=i * BAR, open=v, close=v, high=v + 0.1, low=v - 0.1, volume=10)
        for i, v in enumerate(values)
    ]
    touches = [(i, rows[i].low if up else rows[i].high) for i in (160, 174, 188)]
    return rows, ([] if up else touches), (touches if up else [])


@pytest.mark.parametrize("up", [True, False])
@pytest.mark.parametrize(
    "ending,status", [(104, "confirmed"), (99, "forming"), (87, "invalidated")]
)
def test_three_touch_states(up, ending, status):
    rows, highs, lows = triple(up, ending)
    kind = "triple_bottom" if up else "triple_top"
    result = detect_triple(rows, highs, lows, kind, kind, 0.4, 0.2)
    assert result["status"] == status
    assert len(result["pivot_points"]) == 5
    if status == "confirmed":
        assert result["confirmed_at"] >= result["structure_available_at"]
        assert result["volume_ratio"] == 1
        assert result["breakout_holding"] is True


def test_breakout_must_clear_both_intervening_extremes():
    rows, highs, lows = triple(ending=100.5)
    result = detect_triple(rows, highs, lows, "triple_bottom", "Triple", 0.4, 0.2)
    assert result["breakout_level"] == 101.1
    assert result["status"] == "forming"


def test_unequal_third_touch_rejected():
    rows, highs, lows = triple(third=94)
    result = detect_triple(rows, highs, lows, "triple_bottom", "Triple", 0.4, 0.2)
    assert result["status"] == "not_detected"


def test_registry_and_scan(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal

    rows, _, _ = triple()
    result = next(p for p in detect(rows) if p["pattern"] == "triple_bottom")
    assert result["status"] == "confirmed"
    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
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
        db.add_all([BinanceSpotCandle(symbol="BTCUSDT", interval="15m", **vars(r)) for r in rows])
        db.commit()
    scanned = client.get("/analysis/binance/formations/scan?direction=up&state=confirmed").json()
    assert any(p["pattern"] == "triple_bottom" for p in scanned["matches"][0]["patterns"])
