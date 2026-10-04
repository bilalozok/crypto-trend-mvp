from types import SimpleNamespace

import pytest

from app.services.flag_patterns import detect_flag
from app.services.formations import BAR, detect


def flag(up=True, ending=119, prior=80):
    values = [float(prior)] * 200
    points = [
        (120, prior),
        (150, 120),
        (158, 115.2),
        (166, 118.4),
        (174, 113.6),
        (182, 116.8),
        (190, 112),
        (193, 114),
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
    "ending,status", [(119, "confirmed"), (114, "forming"), (108, "invalidated")]
)
def test_flag_states(up, ending, status):
    rows, highs, lows = flag(up, ending)
    kind = "bull_flag" if up else "bear_flag"
    result = detect_flag(rows, highs, lows, kind, kind, 1, 0.4, 0.2)
    assert result["status"] == status
    assert result["direction"] == ("up" if up else "down")
    assert len(result["pole_points"]) == 2
    assert len(result["boundary_lines"]) == 2
    assert 0 < result["flag_retracement_ratio"] <= 0.6
    if status == "confirmed":
        assert result["confirmed_at"] >= result["structure_available_at"]
        assert result["volume_ratio"] == 1
        assert result["breakout_holding"] is True


@pytest.mark.parametrize("prior", [115, 107])
def test_weak_pole_or_excessive_retracement_rejected(prior):
    rows, highs, lows = flag(prior=prior)
    result = detect_flag(rows, highs, lows, "bull_flag", "Flag", 1, 0.4, 0.2)
    assert result["status"] == "not_detected"


def test_nonparallel_channel_rejected():
    rows, highs, lows = flag()
    lows = [(i, v - j * 2) for j, (i, v) in enumerate(lows)]
    assert (
        detect_flag(rows, highs, lows, "bull_flag", "Flag", 1, 0.4, 0.2)["status"] == "not_detected"
    )


def test_registry_and_plot_metadata():
    rows, _, _ = flag()
    result = next(p for p in detect(rows) if p["pattern"] == "bull_flag")
    assert result["status"] == "confirmed"
    assert result["pole_move_pct"] > 0
    assert len(result["pivot_points"]) == 6


def test_flag_expires_without_confirmation():
    rows, highs, lows = flag(ending=114)
    rows.extend(
        [
            SimpleNamespace(
                open_time=i * BAR, open=114, close=114, high=114.1, low=113.9, volume=10
            )
            for i in range(200, 220)
        ]
    )
    result = detect_flag(rows, highs, lows, "bull_flag", "Flag", 1, 0.4, 0.2)
    assert result["status"] == "invalidated"
    assert result["confirmed_at"] is None
    assert "süresi" in result["reason"]


def test_confirmed_levels_remain_fixed():
    rows, highs, lows = flag()
    first = detect_flag(rows, highs, lows, "bull_flag", "Flag", 1, 0.4, 0.2)
    rows.extend(
        [
            SimpleNamespace(
                open_time=i * BAR, open=119, close=119, high=119.1, low=118.9, volume=10
            )
            for i in range(200, 210)
        ]
    )
    later = detect_flag(rows, highs, lows, "bull_flag", "Flag", 1, 0.4, 0.2)
    assert first["status"] == later["status"] == "confirmed"
    assert first["breakout_level"] == later["breakout_level"]
    assert first["invalidation_level"] == later["invalidation_level"]
