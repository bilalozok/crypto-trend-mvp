from types import SimpleNamespace

import pytest

from app.services.formations import BAR, detect, pivots
from app.services.sloped_patterns import detect_sloped


def structure(ending=100):
    values = [100.0] * 200
    points = [
        (150, 100),
        (160, 110),
        (166, 90),
        (172, 112),
        (178, 88),
        (184, 114),
        (190, 86),
        (194, 100),
        (199, ending),
    ]
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    for i in range(194, 199):
        values[i] = 100
    return [
        SimpleNamespace(open_time=i * BAR, open=v, close=v, high=v + 0.1, low=v - 0.1, volume=10)
        for i, v in enumerate(values)
    ]


def analyze(rows):
    highs, lows = pivots(rows)
    return detect_sloped(rows, highs, lows, "broadening_triangle", "Genişleyen üçgen", 1, 0.4, 0.2)


@pytest.mark.parametrize(
    "ending,direction,status",
    [(100, "neutral", "forming"), (118, "up", "confirmed"), (82, "down", "confirmed")],
)
def test_expanding_channel_states(ending, direction, status):
    result = analyze(structure(ending))
    assert result["status"] == status
    assert result["direction"] == direction
    assert result["apex_time"] is None
    assert len(result["boundary_lines"]) == 2
    if status == "confirmed":
        assert result["confirmed_at"] >= result["structure_available_at"]
        assert result["volume_ratio"] == 1
        assert result["breakout_holding"] is True


def test_fixed_confirmation_levels_and_opposite_invalidation():
    rows = structure()
    rows[194].close = 118
    rows[199].close = 82
    result = analyze(rows)
    assert result["status"] == "invalidated"
    assert result["direction"] == "up"
    assert result["confirmation_age_bars"] == 5
    assert result["breakout_level"] == pytest.approx(115.7666666667)


def test_early_crossing_not_backdated():
    rows = structure()
    rows[191].close = 118
    assert analyze(rows)["confirmed_at"] is None


def test_parallel_lines_rejected():
    rows = structure()
    highs = [(160, 110), (172, 110), (184, 110)]
    lows = [(166, 90), (178, 90), (190, 90)]
    result = detect_sloped(rows, highs, lows, "broadening_triangle", "Broadening", 1, 0.4, 0.2)
    assert result["status"] == "not_detected"


def test_registry():
    result = {p["pattern"]: p for p in detect(structure(118))}
    assert len(result) == 20
    assert result["broadening_triangle"]["status"] == "confirmed"


def test_registry_and_scan(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal

    rows = structure(118)
    result = next(p for p in detect(rows) if p["pattern"] == "broadening_triangle")
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
    assert any(p["pattern"] == "broadening_triangle" for p in scanned["matches"][0]["patterns"])
