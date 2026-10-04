from types import SimpleNamespace

import pytest

from app.services.formations import BAR, detect, pivots
from app.services.rectangle_patterns import detect_rectangle


def structure(up=True, ending=113, rising=0):
    values = [85.0] * 200
    points = [
        (140, 85),
        (152, 105),
        (160, 110),
        (166, 100),
        (172, 110 + rising),
        (178, 100),
        (184, 110 + 2 * rising),
        (190, 100),
        (194, 105),
        (199, ending),
    ]
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    if not up:
        values = [200 - v for v in values]
    return [
        SimpleNamespace(open_time=i * BAR, open=v, close=v, high=v + 0.1, low=v - 0.1, volume=10)
        for i, v in enumerate(values)
    ]


def analyze(rows, up):
    highs, lows = pivots(rows)
    kind = "bull_rectangle" if up else "bear_rectangle"
    return detect_rectangle(rows, highs, lows, kind, kind, 0.4, 0.2)


@pytest.mark.parametrize("up", [True, False])
@pytest.mark.parametrize(
    "ending,status", [(113, "confirmed"), (105, "forming"), (97, "invalidated")]
)
def test_rectangle_states(up, ending, status):
    result = analyze(structure(up, ending), up)
    assert result["status"] == status
    assert len(result["pivot_points"]) == 6
    if status == "confirmed":
        assert result["confirmed_at"] >= result["structure_available_at"]
        assert result["volume_ratio"] == 1
        assert result["breakout_holding"] is True


def test_sloping_resistance_rejected():
    assert analyze(structure(rising=1), True)["status"] == "not_detected"


def test_without_prior_trend_rejected():
    rows = structure()
    rows[148].close = 110
    assert analyze(rows, True)["status"] == "not_detected"


def test_early_breakout_not_backdated():
    rows = structure(ending=105)
    rows[191].close = 113
    assert analyze(rows, True)["confirmed_at"] is None


def test_registry_includes_rectangles():
    results = {p["pattern"]: p for p in detect(structure())}
    assert len(results) == 20
    assert results["bull_rectangle"]["status"] == "confirmed"


def test_registry_and_scan(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal

    rows = structure()
    result = next(p for p in detect(rows) if p["pattern"] == "bull_rectangle")
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
    assert any(p["pattern"] == "bull_rectangle" for p in scanned["matches"][0]["patterns"])
