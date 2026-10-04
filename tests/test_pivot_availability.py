from types import SimpleNamespace

import pytest

from app.services.formations import BAR, detect, evaluate, timestamp


def candles(values):
    return [
        SimpleNamespace(open_time=i * BAR, open=v, close=v, high=v + 0.1, low=v - 0.1, volume=1)
        for i, v in enumerate(values)
    ]


@pytest.mark.parametrize("up", [True, False])
def test_confirmation_waits_for_right_hand_pivot_candles(up):
    values = [95, 95, 101, 101, 101, 101]
    if not up:
        values = [200 - v for v in values]
    status, confirmed = evaluate(candles(values), 1, up, 100, 90 if up else 110, 0.5)
    assert status == "confirmed"
    assert confirmed == timestamp(5 * BAR)


def test_transient_unobservable_breakout_not_confirmed():
    rows = candles([95, 95, 101, 101, 99, 99])
    status, confirmed = evaluate(rows, 1, True, 100, 90, 0.5)
    assert status == "forming"
    assert confirmed is None


def test_detector_confirmation_not_before_structure_availability():
    values = [100.0] * 200
    points = [(160, 100), (170, 90), (178, 100), (186, 90), (187, 102), (199, 102)]
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    result = detect(candles(values))[0]
    assert result["status"] == "confirmed"
    assert result["anchor_time"] == timestamp(186 * BAR)
    assert result["structure_available_at"] == timestamp(190 * BAR)
    assert result["confirmed_at"] == result["structure_available_at"]
    assert result["confirmation_age_bars"] == 10
