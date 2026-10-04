import pytest

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.session import SessionLocal
from app.services.formations import BAR


def populate():
    values = [100.0] * 200
    points = [(160, 100), (170, 90), (178, 100), (186, 90), (191, 97), (199, 102)]
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
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
                    open=v,
                    high=v + 0.1,
                    low=v - 0.1,
                    close=v,
                    volume=10,
                )
                for i, v in enumerate(values)
            ]
        )
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                interval="15m",
                open_time=200 * BAR,
                open=1,
                high=1,
                low=1,
                close=1,
                volume=1,
            )
        )
        db.commit()


def test_chart_uses_same_closed_window_and_pivots(client, monkeypatch):
    populate()
    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
    response = client.get("/analysis/binance/chart-data?symbol=btcusdt")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert len(data["candles"]) == 200
    assert data["candles"][-1]["open_time"] == 199 * BAR
    pattern = data["patterns"][0]
    assert pattern["status"] == "confirmed"
    assert len(pattern["pivot_points"]) == 2
    assert [p["kind"] for p in pattern["pivot_points"]] == ["low", "low"]
    assert [p["price"] for p in pattern["pivot_points"]] == [89.9, 89.9]
    single = client.get("/analysis/binance/formations?symbol=BTCUSDT").json()
    assert single["patterns"] == data["patterns"]


@pytest.mark.parametrize(
    "case,status,visible",
    [
        ("stale", "stale_data", True),
        ("invalid", "invalid_data", False),
    ],
)
def test_non_ready_data_has_no_overlay(client, monkeypatch, case, status, visible):
    populate()
    if case == "invalid":
        with SessionLocal() as db:
            row = db.get(BinanceSpotCandle, ("BTCUSDT", 50 * BAR))
            row.high = 1
            db.commit()
    monkeypatch.setattr("app.main.now_ms", lambda: (203 if case == "stale" else 200) * BAR)
    data = client.get("/analysis/binance/chart-data?symbol=BTCUSDT").json()
    assert data["status"] == status
    assert data["patterns"] == []
    assert bool(data["candles"]) is visible


def test_chart_html_and_unknown_symbol(client):
    page = client.get("/analysis/binance/chart?symbol=BTCUSDT")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert '<svg id="chart"' in page.text
    assert "/analysis/binance/chart-data" in page.text
    assert "https://" not in page.text
    assert client.get("/analysis/binance/chart-data?symbol=UNKNOWN").status_code == 404
    assert client.get("/analysis/binance/chart-data?symbol=BTC/USDT").status_code == 422
