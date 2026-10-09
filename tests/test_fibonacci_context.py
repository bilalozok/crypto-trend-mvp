from types import SimpleNamespace

import pytest

from app.services.fibonacci_context import context, levels


def rows(prices):
    return [
        SimpleNamespace(open_time=i * 900_000, high=p + 1, low=p - 1) for i, p in enumerate(prices)
    ]


def test_directional_retracements():
    assert levels(100, 200)[2]["price"] == 150
    assert levels(200, 100)[2]["price"] == 150
    assert levels(100, 200)[3]["price"] == pytest.approx(138.2)
    assert levels(200, 100)[3]["price"] == pytest.approx(161.8)


def test_pivot_pair_requires_three_closed_right_bars():
    data = rows([10, 9, 8, 5, 8, 9, 10, 15, 10, 9, 8])
    assert context(data[:-1], 900_000)["status"] == "no_pivot_pair"
    result = context(data, 900_000)
    assert result["status"] == "ready"
    assert result["direction"] == "up"
    assert result["start"]["price"] == 4
    assert result["end"]["price"] == 16
    assert result["end"]["observable_at"].timestamp() * 1000 == 11 * 900_000
    assert (
        context(rows([20 - p for p in [10, 9, 8, 5, 8, 9, 10, 15, 10, 9, 8]]), 900_000)["direction"]
        == "down"
    )


def test_flat_ambiguous_and_invalid_data_have_no_levels():
    assert context(rows([10] * 200), 900_000)["levels"] == []
    data = rows([10, 9, 8, 5, 8, 9, 10, 15, 10, 9, 8])
    data[7].low = 1  # both extrema on one candle: no known intrabar order
    assert context(data, 900_000)["levels"] == []
    data[0].high = float("nan")
    assert context(data, 900_000)["levels"] == []
