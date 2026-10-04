from types import SimpleNamespace

import pytest

from app.services.formations import BAR, confirmation_volume, timestamp


def rows():
    return [SimpleNamespace(open_time=i * BAR, volume=10) for i in range(40)]


@pytest.mark.parametrize(
    "value,ratio,supported",
    [
        (15, 1.5, True),
        (14, 1.4, False),
        (0, 0, False),
        (20, 2, True),
    ],
)
def test_volume_ratio_excludes_confirmation_and_future(value, ratio, supported):
    data = rows()
    data[25].volume = value
    data[26].volume = 1_000_000
    result = confirmation_volume(data, timestamp(26 * BAR))
    assert result["prior_volume_average"] == 10
    assert result["volume_ratio"] == ratio
    assert result["volume_supported"] is supported


def test_zero_average_and_short_history_are_unknown():
    data = rows()
    for row in data[:25]:
        row.volume = 0
    result = confirmation_volume(data, timestamp(26 * BAR))
    assert result["prior_volume_average"] == 0
    assert result["volume_ratio"] is None
    assert result["volume_supported"] is None
    short = confirmation_volume(rows(), timestamp(11 * BAR))
    assert short["confirmation_volume"] == 10
    assert short["prior_volume_average"] is None
    assert confirmation_volume(data, None)["volume_ratio"] is None


def test_no_future_information_in_volume_ratio():
    data = rows()
    first = confirmation_volume(data, timestamp(26 * BAR))
    for row in data[26:]:
        row.volume = 1_000_000
    assert confirmation_volume(data, timestamp(26 * BAR)) == first


def test_scan_volume_filter(client, monkeypatch):
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
                BinanceSpotCandle(
                    symbol="BTCUSDT",
                    interval="15m",
                    open_time=i * BAR,
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=10,
                )
                for i in range(200)
            ]
        )
        db.commit()
    monkeypatch.setattr(
        "app.services.formations.detect",
        lambda data: [
            {"status": "confirmed", "direction": "up", "volume_ratio": 1.5},
            {"status": "confirmed", "direction": "up", "volume_ratio": None},
        ],
    )
    base = "/analysis/binance/formations/scan?min_volume_ratio="
    assert len(client.get(base + "1.5").json()["matches"][0]["patterns"]) == 1
    assert client.get(base + "1.6").json()["matched_symbols"] == 0
    assert (
        len(client.get("/analysis/binance/formations/scan").json()["matches"][0]["patterns"]) == 2
    )
    for value in ("-1", "1001", "nan", "inf"):
        assert client.get(base + value).status_code == 422


def test_detector_attaches_first_confirmation_volume():
    from app.services.formations import detect

    values = [100.0] * 200
    points = [(160, 100), (170, 90), (178, 100), (186, 90), (191, 97), (199, 102)]
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    data = [
        SimpleNamespace(open_time=i * BAR, open=v, close=v, high=v + 0.1, low=v - 0.1, volume=10)
        for i, v in enumerate(values)
    ]
    first = detect(data)[0]
    index = round(first["confirmed_at"].timestamp() * 1000) // BAR - 1
    data[index].volume = 20
    result = detect(data)[0]
    assert result["volume_ratio"] == 2
    assert result["volume_supported"] is True
