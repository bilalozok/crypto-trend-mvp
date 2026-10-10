from types import SimpleNamespace

import pytest

from app.services.bullish_candidates import rank_match
from app.services.money_flow_context import calculate, candidate_context
from tests.test_bullish_candidates import pattern, row


def candles(n=200, falling=False, volume=2):
    return [
        SimpleNamespace(
            open_time=i * 900000,
            high=(400 - i if falling else 100 + i) + 2,
            low=(400 - i if falling else 100 + i) - 2,
            close=(400 - i if falling else 100 + i) + (-1 if falling else 1),
            volume=volume,
        )
        for i in range(n)
    ]


def test_cmf_mfi_known_values_and_direction():
    for falling, expected_cmf, expected_mfi, expected_adjustment in [
        (False, 0.5, 100, 10),
        (True, -0.5, 0, -10),
    ]:
        data = candles(falling=falling)
        latest = calculate(data, 900000)["latest"]
        assert latest["cmf"] == pytest.approx(expected_cmf)
        assert latest["mfi"] == expected_mfi
        c = candidate_context(data, 200 * 900000)
        assert c["adjustment"] == expected_adjustment
        assert c["mfi_extreme"]


def test_zero_range_zero_volume_and_equal_typical():
    data = [
        SimpleNamespace(open_time=i * 900000, high=100, low=100, close=100, volume=1)
        for i in range(200)
    ]
    latest = calculate(data, 900000)["latest"]
    assert latest["cmf"] == 0
    assert latest["mfi"] == 50
    assert calculate(candles(volume=0), 900000)["status"] == "insufficient_data"
    assert candidate_context(candles(volume=0), 200 * 900000)["adjustment"] == 0


def test_invalid_and_no_lookahead():
    data = candles()
    a = calculate(data[:90], 900000)["latest"]
    b = calculate(data[:100], 900000)["series"][89]
    assert a == b
    data[-1].volume = -1
    assert calculate(data, 900000)["status"] == "invalid_data"
    assert candidate_context(candles(), 201 * 900000)["status"] == "invalid_data"
    data = candles()
    data[100].open_time += 1
    assert candidate_context(data, 200 * 900000)["status"] == "invalid_data"


def test_ranking_context_bounded_and_eligibility_preserved():
    data = row([pattern(distance=0)])
    base = rank_match(data)[0]["evidence_score"]
    context = candidate_context(candles(), 200 * 900000)
    data["ranking_indicator_context"] = context
    result = rank_match(data)[0]
    assert result["base_evidence_score"] == base
    assert result["evidence_score"] == 100
    assert result["indicator_adjustment"] == 10
    data["patterns"] = []
    assert rank_match(data)[0] is None
    data["patterns"] = [pattern()]
    data["ranking_indicator_context"] = {"status": "unavailable", "adjustment": 10}
    assert rank_match(data)[0]["indicator_adjustment"] == 0
