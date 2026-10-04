from types import SimpleNamespace

import pytest

from app.services.formations import BAR, detect


def structure(up=False, ending=98, right_shoulder=108, head=115, neck2=100):
    points = [
        (145, 100),
        (155, 108),
        (163, 100),
        (172, head),
        (180, neck2),
        (189, right_shoulder),
        (194, 104),
        (199, ending),
    ]
    values = [100.0] * 200
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    if up:
        values = [200 - v for v in values]
    return [
        SimpleNamespace(open_time=i * BAR, open=v, close=v, high=v + 0.1, low=v - 0.1, volume=10)
        for i, v in enumerate(values)
    ]


@pytest.mark.parametrize("up", [False, True])
@pytest.mark.parametrize(
    "ending,status", [(98, "confirmed"), (104, "forming"), (111, "invalidated")]
)
def test_pattern_states(up, ending, status):
    kind = "inverse_head_and_shoulders" if up else "head_and_shoulders"
    result = next(p for p in detect(structure(up=up, ending=ending)) if p["pattern"] == kind)
    assert result["status"] == status
    assert result["direction"] == ("up" if up else "down")
    assert len(result["pivot_points"]) == 5
    assert {p["label"] for p in result["pivot_points"]} == {"Sol omuz", "Baş", "Sağ omuz", "Boyun"}
    if status == "confirmed":
        assert result["confirmed_at"] >= result["structure_available_at"]
        assert result["volume_ratio"] == 1
        assert result["breakout_holding"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"right_shoulder": 112},
        {"head": 108.2},
        {"neck2": 103},
    ],
)
def test_rejects_unequal_shoulders_small_head_or_sloping_neck(kwargs):
    result = next(p for p in detect(structure(**kwargs)) if p["pattern"] == "head_and_shoulders")
    assert result["status"] == "not_detected"


def test_chart_and_scan_use_expanded_pattern_registry(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal

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
        db.add_all(
            [
                BinanceSpotCandle(symbol="BTCUSDT", interval="15m", **vars(row))
                for row in structure(up=True)
            ]
        )
        db.commit()
    chart = client.get("/analysis/binance/chart-data?symbol=BTCUSDT").json()
    assert len(chart["patterns"]) == 6
    result = client.get("/analysis/binance/formations/scan?direction=up&state=confirmed").json()
    names = {p["pattern"] for p in result["matches"][0]["patterns"]}
    assert "inverse_head_and_shoulders" in names
