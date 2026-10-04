import pytest
from sqlalchemy import event

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.session import SessionLocal, engine
from app.services.formation_scan import scan
from app.services.formations import BAR


def seed(db, symbol, base="BTC", volume=10, count=200):
    db.add(
        BinanceSpotSymbol(
            symbol=symbol,
            base_asset=base,
            active=True,
            quote_volume_24h=volume,
            catalog_updated_ms=0,
        )
    )
    for i in range(count):
        db.add(
            BinanceSpotCandle(
                symbol=symbol,
                interval="15m",
                open_time=i * BAR,
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1,
            )
        )
    db.commit()


def fake_patterns(rows):
    return [
        {"pattern": "double_bottom", "status": "confirmed", "direction": "up"},
        {"pattern": "double_top", "status": "forming", "direction": "down"},
        {"pattern": "ascending_triangle", "status": "not_detected", "direction": "up"},
    ]


def test_scan_filters_quality_and_pagination(monkeypatch):
    monkeypatch.setattr("app.services.formations.detect", fake_patterns)
    with SessionLocal() as db:
        seed(db, "BTCUSDT", volume=30)
        seed(db, "ETHUSDT", base="ETH", volume=20, count=10)
        seed(db, "USDCUSDT", base="USDC", volume=100)
        first = scan(db, 200 * BAR, limit=1, direction="up")
        assert first["total_eligible_symbols"] == 2
        assert first["matched_symbols"] == 1
        assert len(first["matches"][0]["patterns"]) == 1
        assert first["next_offset"] == 1
        second = scan(db, 200 * BAR, offset=1)
        assert second["quality_counts"] == {"insufficient_data": 1}
        assert second["matches"] == []
        assert second["next_offset"] is None
        included = scan(db, 200 * BAR, min_volume=50, include_stablecoins=True)
        assert included["matches"][0]["symbol"] == "USDCUSDT"


@pytest.mark.parametrize(
    "direction,state,count",
    [
        ("up", "forming", 0),
        ("down", "forming", 1),
        ("all", "all", 2),
        ("down", "invalidated", 0),
    ],
)
def test_pattern_filters(monkeypatch, direction, state, count):
    monkeypatch.setattr("app.services.formations.detect", fake_patterns)
    with SessionLocal() as db:
        seed(db, "BTCUSDT")
        result = scan(db, 200 * BAR, direction=direction, state=state)
        patterns = result["matches"][0]["patterns"] if result["matches"] else []
        assert len(patterns) == count


def test_constant_query_count_and_stale_data():
    calls = []

    def record(*args):
        calls.append(1)

    with SessionLocal() as db:
        seed(db, "BTCUSDT")
        seed(db, "ETHUSDT", base="ETH")
        event.listen(engine, "before_cursor_execute", record)
        try:
            result = scan(db, 202 * BAR)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert len(calls) == 3
        assert result["quality_counts"] == {"stale_data": 2}
        assert result["matches"] == []


def test_empty_api_and_validation(client):
    result = client.get("/analysis/binance/formations/scan")
    assert result.status_code == 200
    assert result.json()["scanned_symbols"] == 0
    assert result.json()["next_offset"] is None
    for query in ("limit=101", "direction=sideways", "state=buy", "min_quote_volume=-1"):
        assert client.get("/analysis/binance/formations/scan?" + query).status_code == 422


def test_real_double_bottom_through_endpoint(client, monkeypatch):
    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
    with SessionLocal() as db:
        seed(db, "BTCUSDT")
        points = [(160, 100), (170, 90), (178, 100), (186, 90), (191, 97), (199, 102)]
        for (a, x), (b, y) in zip(points, points[1:], strict=False):
            for i in range(a, b + 1):
                value = x + (y - x) * (i - a) / (b - a)
                row = db.get(BinanceSpotCandle, ("BTCUSDT", i * BAR))
                row.open = row.close = value
                row.high, row.low = value + 0.1, value - 0.1
        db.commit()
    response = client.get("/analysis/binance/formations/scan?direction=up&state=confirmed")
    assert response.status_code == 200
    data = response.json()
    assert data["matched_symbols"] == 1
    assert data["matches"][0]["patterns"][0]["pattern"] == "double_bottom"
