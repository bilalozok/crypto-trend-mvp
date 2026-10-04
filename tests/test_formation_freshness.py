from types import SimpleNamespace

import pytest

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.session import SessionLocal
from app.services.formations import BAR, detect


def structure(mirror=False, ending=102):
    values = [100.0] * 200
    points = [(160, 100), (170, 90), (178, 100), (186, 90), (191, 97), (199, ending)]
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    if mirror:
        values = [200 - v for v in values]
    return [
        SimpleNamespace(open_time=i * BAR, open=v, close=v, high=v + 0.1, low=v - 0.1, volume=1)
        for i, v in enumerate(values)
    ]


@pytest.mark.parametrize("mirror,position", [(False, "above"), (True, "below")])
def test_confirmed_age_direction_and_distance(mirror, position):
    rows = structure(mirror)
    kind = "double_top" if mirror else "double_bottom"
    result = next(p for p in detect(rows) if p["pattern"] == kind)
    assert result["status"] == "confirmed"
    assert result["breakout_holding"] is True
    assert result["breakout_position"] == position
    confirmed_ms = round(result["confirmed_at"].timestamp() * 1000)
    assert result["confirmation_age_bars"] == (200 * BAR - confirmed_ms) // BAR
    expected = (rows[-1].close / result["breakout_level"] - 1) * 100
    assert result["distance_from_breakout_pct"] == pytest.approx(expected)


def test_confirmation_retained_after_pullback():
    rows = structure()
    rows[-1].close = rows[-1].open = 99
    rows[-1].high, rows[-1].low = 99.1, 98.9
    result = detect(rows)[0]
    assert result["status"] == "confirmed"
    assert result["breakout_holding"] is False
    assert result["confirmation_age_bars"] > 0


def test_forming_and_not_detected_have_no_confirmation_age():
    result = detect(structure(ending=97))[0]
    assert result["status"] == "forming"
    assert result["confirmation_age_bars"] is None
    rows = structure()
    for row in rows:
        row.open = row.close = 100
        row.high, row.low = 101, 99
    for result in detect(rows):
        assert result["status"] == "not_detected"
        assert result["breakout_holding"] is None
        assert result["distance_from_breakout_pct"] is None


def test_freshness_filters_through_api(client, monkeypatch):
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
                for row in structure()
            ]
        )
        db.commit()
    base = "/analysis/binance/formations/scan?direction=up&state=confirmed"
    data = client.get(base).json()
    age = data["matches"][0]["patterns"][0]["confirmation_age_bars"]
    assert client.get(base + f"&max_confirmation_age_bars={age}").json()["matched_symbols"] == 1
    assert client.get(base + "&max_confirmation_age_bars=0").json()["matched_symbols"] == 0
    assert client.get(base + "&breakout_holding=true").json()["matched_symbols"] == 1
    assert client.get(base + "&breakout_holding=false").json()["matched_symbols"] == 0
    for query in (
        "max_confirmation_age_bars=-1",
        "max_confirmation_age_bars=201",
        "breakout_holding=invalid",
    ):
        assert client.get(base + "&" + query).status_code == 422


def test_latest_confirmation_has_zero_age():
    rows = structure(ending=100)
    rows[-1].open = rows[-1].close = 102
    rows[-1].high, rows[-1].low = 102.1, 101.9
    result = detect(rows)[0]
    assert result["status"] == "confirmed"
    assert result["confirmation_age_bars"] == 0
