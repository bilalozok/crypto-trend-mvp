from types import SimpleNamespace

import pytest

from app.services.cup_patterns import detect_cup
from app.services.formations import BAR, detect, pivots


def structure(up=True, ending=113, handle=107, sharp=False, right=110):
    values = [105.0] * 200
    for i in range(130, 141):
        values[i] = 105 + (i - 130) * 0.5
    for i in range(140, 181):
        x = (i - 160) / 20
        values[i] = 100 + 10 * (abs(x) if sharp else x * x)
    values[180] = right
    for (a, x), (b, y) in zip(
        [(180, right), (188, handle), (194, 109), (199, ending)],
        [(188, handle), (194, 109), (199, ending)],
        strict=False,
    ):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    if not up:
        values = [200 - v for v in values]
    return [
        SimpleNamespace(open_time=i * BAR, open=v, close=v, high=v + 0.1, low=v - 0.1, volume=10)
        for i, v in enumerate(values)
    ]


def analyze(rows, up=True):
    highs, lows = pivots(rows)
    kind = "cup_and_handle" if up else "inverse_cup_and_handle"
    return detect_cup(rows, highs, lows, kind, kind, 0.4, 0.2)


@pytest.mark.parametrize("up", [True, False])
@pytest.mark.parametrize(
    "ending,status", [(113, "confirmed"), (109, "forming"), (105, "invalidated")]
)
def test_cup_states(up, ending, status):
    result = analyze(structure(up, ending), up)
    assert result["status"] == status
    assert len(result["pivot_points"]) == 4
    if status == "confirmed":
        assert result["confirmed_at"] >= result["structure_available_at"]
        assert result["volume_ratio"] == 1
        assert result["breakout_holding"] is True


def test_deep_handle_rejected():
    assert analyze(structure(handle=103))["status"] == "not_detected"


def test_unequal_rims_rejected():
    assert analyze(structure(right=112))["status"] == "not_detected"


def test_single_sharp_v_rejected():
    rows = structure()
    for i in range(141, 180):
        rows[i].close = rows[i].high = 109
        rows[i].low = 108.9
    rows[160].close = 100
    rows[160].low = 99.9
    assert analyze(rows)["status"] == "not_detected"


def test_early_breakout_not_backdated():
    rows = structure(ending=109)
    rows[189].close = 113
    assert analyze(rows)["confirmed_at"] is None


def test_registry_includes_cups():
    results = {p["pattern"]: p for p in detect(structure())}
    assert len(results) == 20
    assert results["cup_and_handle"]["status"] == "confirmed"


def test_registry_and_scan(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal

    rows = structure()
    result = next(p for p in detect(rows) if p["pattern"] == "cup_and_handle")
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
    assert any(p["pattern"] == "cup_and_handle" for p in scanned["matches"][0]["patterns"])
