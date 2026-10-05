from types import SimpleNamespace

import pytest

from app.services.formations import BAR
from app.services.signal_backtest import replay
from tests.test_formation_freshness import structure


def confirmed_history():
    data = structure(ending=100)
    data[-1].open = data[-1].close = 102
    data[-1].high, data[-1].low, data[-1].volume = 102.1, 101.9, 4
    return data


def identities(result):
    fields = ("signal_time", "pattern", "name", "evidence_score", "volume_ratio", "breakout_level")
    return [tuple(row[key] for key in fields) for row in result["signals"] + result["pending"]]


@pytest.mark.parametrize("future_price", [1, 104, 10000])
def test_real_detector_signal_unchanged_when_future_is_appended(future_price):
    prefix = confirmed_history()
    before = replay(prefix, "BTCUSDT", 200 * BAR)
    assert len(before["pending"]) == 1
    full = prefix + [
        SimpleNamespace(
            open_time=i * BAR,
            open=future_price,
            close=future_price,
            high=future_price * 1.01,
            low=future_price * 0.99,
            volume=1000000,
        )
        for i in range(200, 208)
    ]
    after = replay(full, "BTCUSDT", 208 * BAR)
    original_time = before["pending"][0]["signal_time"]
    same_time = [identity for identity in identities(after) if identity[0] == original_time]
    assert same_time == identities(before)
    trade = next(row for row in after["signals"] if row["signal_time"] == original_time)
    assert trade["entry_time"] == original_time
    assert trade["entry_open"] == future_price


def test_horizon_changes_outcomes_without_changing_real_signal_selection():
    data = confirmed_history() + [
        SimpleNamespace(open_time=i * BAR, open=102, close=104, high=105, low=101, volume=1)
        for i in range(200, 216)
    ]
    results = [replay(data, "BTCUSDT", 216 * BAR, horizon=h) for h in (4, 8, 16)]
    assert identities(results[0])
    assert identities(results[0]) == identities(results[1]) == identities(results[2])
    for horizon, result in zip((4, 8, 16), results, strict=True):
        trade = result["signals"][0]
        assert (trade["exit_time"] - trade["entry_time"]).total_seconds() == horizon * 900
