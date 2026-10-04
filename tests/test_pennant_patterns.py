from types import SimpleNamespace

import pytest

from app.services.formations import BAR, detect
from app.services.pennant_patterns import detect_pennant


def pennant(up=True, ending=122, prior=80):
    values = [float(prior)] * 200
    points = [
        (120, prior),
        (150, 120),
        (158, 116.24),
        (166, 119.52),
        (174, 116.72),
        (182, 119.04),
        (190, 117.2),
        (193, 118),
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
    highs = [(i, rows[i].high) for i in ([150, 166, 182] if up else [158, 174, 190])]
    lows = [(i, rows[i].low) for i in ([158, 174, 190] if up else [150, 166, 182])]
    return rows, highs, lows


@pytest.mark.parametrize("up", [True, False])
@pytest.mark.parametrize(
    "ending,status", [(122, "confirmed"), (118, "forming"), (114, "invalidated")]
)
def test_directional_pennant_states(up, ending, status):
    rows, highs, lows = pennant(up, ending)
    kind = "bull_pennant" if up else "bear_pennant"
    result = detect_pennant(rows, highs, lows, kind, kind, 1, 0.4, 0.2)
    assert result["status"] == status
    assert result["direction"] == ("up" if up else "down")
    assert len(result["pole_points"]) == 2
    assert len(result["boundary_lines"]) == 2
    assert result["pennant_retracement_ratio"] <= 0.6
    if status == "confirmed":
        assert result["confirmed_at"] >= result["structure_available_at"]
        assert result["volume_ratio"] == 1
        assert result["breakout_holding"] is True
    elif status == "invalidated":
        assert result["confirmed_at"] is None


def test_weak_pole_rejected():
    rows, highs, lows = pennant(prior=116)
    result = detect_pennant(rows, highs, lows, "bull_pennant", "Pennant", 1, 0.4, 0.2)
    assert result["status"] == "not_detected"
    assert result["pole_points"] == []


def test_registry_detects_continuation_pennant():
    rows, _, _ = pennant()
    result = next(p for p in detect(rows) if p["pattern"] == "bull_pennant")
    assert result["status"] == "confirmed"
    assert result["pole_move_pct"] > 0


def test_pennant_volume_filter_and_chart(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal

    rows, _, _ = pennant()
    pattern = next(p for p in detect(rows) if p["pattern"] == "bull_pennant")
    confirmation_index = round(pattern["confirmed_at"].timestamp() * 1000) // BAR - 1
    rows[confirmation_index].volume = 20
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
    chart = client.get("/analysis/binance/chart-data?symbol=BTCUSDT").json()
    result = next(p for p in chart["patterns"] if p["pattern"] == "bull_pennant")
    assert result["volume_ratio"] == 2
    assert len(result["pole_points"]) == 2
    scan = client.get(
        "/analysis/binance/formations/scan?direction=up&state=confirmed&min_volume_ratio=1.5"
    ).json()
    assert any(p["pattern"] == "bull_pennant" for p in scan["matches"][0]["patterns"])
