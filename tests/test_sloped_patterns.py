from types import SimpleNamespace

import pytest

from app.services.formations import BAR, detect
from app.services.sloped_patterns import detect_sloped, fit


def channel(kind="symmetrical_triangle", ending=120, reverse=False, convergence=0.2):
    parameters = {
        "rising_wedge": (100, 0.2, 80, 0.5),
        "falling_wedge": (120, -0.5, 100, -0.2),
        "symmetrical_triangle": (120, -convergence, 80, convergence),
    }
    ub, us, lb, ls = parameters[kind]

    def upper(i):
        return ub + us * (i - 150)

    def lower(i):
        return lb + ls * (i - 150)

    indices = [150, 158, 166, 174, 182, 190]
    high_indices = indices[1::2] if reverse else indices[::2]
    low_indices = indices[::2] if reverse else indices[1::2]
    points = [(i, upper(i) if i in high_indices else lower(i)) for i in indices]
    points = [(140, 100)] + points + [(193, 100), (199, ending)]
    values = [100.0] * 200
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    rows = [
        SimpleNamespace(open_time=i * BAR, open=v, close=v, high=v + 0.1, low=v - 0.1, volume=10)
        for i, v in enumerate(values)
    ]
    highs = [(i, rows[i].high) for i in high_indices]
    lows = [(i, rows[i].low) for i in low_indices]
    return rows, highs, lows


@pytest.mark.parametrize(
    "kind,ending,direction,reverse",
    [
        ("symmetrical_triangle", 120, "up", False),
        ("symmetrical_triangle", 80, "down", True),
        ("rising_wedge", 95, "down", True),
        ("falling_wedge", 105, "up", False),
    ],
)
def test_sloped_directional_confirmation(kind, ending, direction, reverse):
    rows, highs, lows = channel(kind, ending, reverse)
    result = detect_sloped(rows, highs, lows, kind, kind, 2, 1, 0.2)
    assert result["status"] == "confirmed"
    assert result["direction"] == direction
    assert len(result["boundary_lines"]) == 2
    assert len(result["pivot_points"]) == 6
    assert result["confirmed_at"] >= result["structure_available_at"]
    assert result["breakout_holding"] is True
    assert result["volume_ratio"] == 1


def test_symmetrical_triangle_forming_has_no_fixed_direction():
    rows, highs, lows = channel(ending=100)
    result = detect_sloped(rows, highs, lows, "symmetrical_triangle", "Triangle", 2, 1, 0.2)
    assert result["status"] == "forming"
    assert result["direction"] == "neutral"
    assert result["breakout_level"] is None
    assert result["breakout_holding"] is None


def test_opposite_wedge_breakout_is_invalidated():
    rows, highs, lows = channel("rising_wedge", ending=120)
    for r in rows[193:]:
        r.open = r.close = 120
        r.high, r.low = 120.1, 119.9
    result = detect_sloped(rows, highs, lows, "rising_wedge", "Wedge", 2, 1, 0.2)
    assert result["status"] == "invalidated"
    assert result["confirmed_at"] is None


def test_rejects_nonconverging_or_wrong_slope_channel():
    rows, highs, lows = channel()
    lows = [(i, 80) for i, _ in lows]
    result = detect_sloped(rows, highs, lows, "symmetrical_triangle", "Triangle", 2, 1, 0.2)
    assert result["status"] == "not_detected"


def test_outside_channel_before_anchor_rejected():
    rows, highs, lows = channel()
    rows[170].high = 130
    result = detect_sloped(rows, highs, lows, "symmetrical_triangle", "Triangle", 2, 1, 0.2)
    assert result["status"] == "not_detected"


def test_fit_and_detector_registry():
    assert fit([(1, 3), (2, 5), (3, 7)]) == (2, 1, 0)
    rows, _, _ = channel()
    result = next(p for p in detect(rows) if p["pattern"] == "symmetrical_triangle")
    assert result["status"] == "confirmed"


def test_confirmed_levels_do_not_follow_projection_after_apex():
    rows, highs, lows = channel("rising_wedge", ending=95, reverse=True)
    first = detect_sloped(rows, highs, lows, "rising_wedge", "Wedge", 2, 1, 0.2)
    rows.extend(
        [
            SimpleNamespace(open_time=i * BAR, open=95, close=95, high=95.1, low=94.9, volume=10)
            for i in range(200, 220)
        ]
    )
    later = detect_sloped(rows, highs, lows, "rising_wedge", "Wedge", 2, 1, 0.2)
    assert first["status"] == later["status"] == "confirmed"
    assert first["breakout_level"] == later["breakout_level"]
    assert first["invalidation_level"] == later["invalidation_level"]
    assert later["confirmation_age_bars"] == first["confirmation_age_bars"] + 20


def test_confirmed_pattern_can_invalidate_after_breakout():
    rows, highs, lows = channel("rising_wedge", ending=120)
    result = detect_sloped(rows, highs, lows, "rising_wedge", "Wedge", 2, 1, 0.2)
    assert result["status"] == "invalidated"
    assert result["confirmed_at"] is not None


def test_unconfirmed_pattern_expires_at_apex():
    rows, highs, lows = channel(ending=100, convergence=0.3)
    rows.extend(
        [
            SimpleNamespace(open_time=i * BAR, open=100, close=100, high=100.1, low=99.9, volume=10)
            for i in range(200, 220)
        ]
    )
    result = detect_sloped(rows, highs, lows, "symmetrical_triangle", "Triangle", 2, 1, 0.2)
    assert result["status"] == "invalidated"
    assert result["confirmed_at"] is None
    assert "süresi doldu" in result["reason"]


def test_sloped_chart_data_and_scan(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal

    rows, _, _ = channel()
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
    data = client.get("/analysis/binance/chart-data?symbol=BTCUSDT").json()
    pattern = next(p for p in data["patterns"] if p["pattern"] == "symmetrical_triangle")
    assert pattern["status"] == "confirmed"
    assert len(pattern["boundary_lines"]) == 2
    scanned = client.get("/analysis/binance/formations/scan?direction=up&state=confirmed").json()
    assert any(p["pattern"] == "symmetrical_triangle" for p in scanned["matches"][0]["patterns"])
