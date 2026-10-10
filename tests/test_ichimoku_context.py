from types import SimpleNamespace

from app.services.ichimoku_context import calculate


def rows(n=100):
    return [
        SimpleNamespace(open_time=i * 900000, high=100 + i + 2, low=100 + i - 2, close=100 + i)
        for i in range(n)
    ]


def test_windows_and_displacement():
    result = calculate(rows(), 900000)
    p = result["latest"]
    assert p["tenkan"] == 195
    assert p["kijun"] == 186.5
    assert p["projected_b"] == 173.5
    assert p["cloud_b"] == 147.5
    assert p["cloud_a"] == 164.75
    assert result["position"] == "above"
    assert result["chikou_reference"] == 173


def test_no_future_leak_and_warmup():
    assert calculate(rows(77), 900000)["status"] == "insufficient_data"
    a = calculate(rows(90), 900000)["latest"]
    b = calculate(rows(100), 900000)["series"][89]
    assert a == b


def test_bad_high_low_and_flat():
    data = rows()
    data[-1].low = data[-1].high + 1
    assert calculate(data, 900000)["status"] == "invalid_data"
    data = [SimpleNamespace(open_time=i * 900000, high=100, low=100, close=100) for i in range(100)]
    assert calculate(data, 900000)["position"] == "inside"
