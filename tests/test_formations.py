from types import SimpleNamespace

import pytest

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.session import SessionLocal
from app.services.formations import BAR, analyze, detect, evaluate, pivots


def rows_from_points(points):
    values = [100.0] * 200
    for (a, x), (b, y) in zip(points, points[1:], strict=False):
        for i in range(a, b + 1):
            values[i] = x + (y - x) * (i - a) / (b - a)
    return [
        SimpleNamespace(open_time=i * BAR, open=v, high=v + 0.1, low=v - 0.1, close=v, volume=10)
        for i, v in enumerate(values)
    ]


@pytest.mark.parametrize("kind,mirror", [("double_bottom", False), ("double_top", True)])
@pytest.mark.parametrize(
    "ending,expected", [(102, "confirmed"), (97, "forming"), (88, "invalidated")]
)
def test_double_patterns(kind, mirror, ending, expected):
    rows = rows_from_points(
        [(0, 100), (160, 100), (170, 90), (178, 100), (186, 90), (191, 97), (199, ending)]
    )
    if mirror:
        for r in rows:
            r.open, r.close, r.high, r.low = 200 - r.open, 200 - r.close, 200 - r.low, 200 - r.high
    result = next(r for r in detect(rows) if r["pattern"] == kind)
    assert result["status"] == expected
    assert result["breakout_level"] is not None


@pytest.mark.parametrize("mirror", [False, True])
def test_triangle_patterns(mirror):
    rows = rows_from_points(
        [
            (0, 100),
            (150, 90),
            (155, 100),
            (160, 91),
            (165, 100),
            (170, 94),
            (175, 100),
            (180, 97),
            (199, 103),
        ]
    )
    kind = "ascending_triangle"
    if mirror:
        kind = "descending_triangle"
        for r in rows:
            r.open, r.close, r.high, r.low = 200 - r.open, 200 - r.close, 200 - r.low, 200 - r.high
    result = next(r for r in detect(rows) if r["pattern"] == kind)
    assert result["status"] == "confirmed"


def test_flat_data_no_patterns_or_pivots():
    rows = rows_from_points([(0, 100), (199, 100)])
    assert pivots(rows) == ([], [])
    assert all(r["status"] == "not_detected" for r in detect(rows))


def test_wick_does_not_confirm_and_invalidation_after_confirmation():
    rows = rows_from_points([(0, 99), (199, 99)])
    rows[198].high = 110
    assert evaluate(rows, 197, True, 100, 90, 0.5)[0] == "forming"
    rows[198].close = 101
    rows[199].close = 89
    status, confirmed = evaluate(rows, 197, True, 100, 90, 0.5)
    assert status == "invalidated"
    assert confirmed is not None


@pytest.mark.parametrize(
    "case,status",
    [
        ("ready", "ready"),
        ("short", "insufficient_data"),
        ("gap", "missing_data"),
        ("stale", "stale_data"),
        ("invalid", "invalid_data"),
    ],
)
def test_data_quality(case, status):
    db = SessionLocal()
    db.add(
        BinanceSpotSymbol(
            symbol="BTCUSDT",
            base_asset="BTC",
            active=True,
            quote_volume_24h=1,
            catalog_updated_ms=0,
        )
    )
    rows = rows_from_points([(0, 100), (199, 100)])
    if case == "short":
        rows.pop()
    if case == "gap":
        rows[50].open_time -= BAR
        rows[50].open_time -= 200 * BAR
    if case == "invalid":
        rows[50].high = 1
    for r in rows:
        db.add(BinanceSpotCandle(symbol="BTCUSDT", interval="15m", **vars(r)))
    # An open candle must not contaminate the analysis.
    db.add(
        BinanceSpotCandle(
            symbol="BTCUSDT",
            interval="15m",
            open_time=200 * BAR,
            open=1,
            high=1,
            low=1,
            close=1,
            volume=0,
        )
    )
    db.commit()
    stamp = 202 * BAR if case == "stale" else 200 * BAR
    result = analyze(db, "BTCUSDT", stamp)
    assert result["status"] == status
    assert bool(result["patterns"]) == (status == "ready")
    db.close()


def test_unknown_and_input_validation(client):
    assert client.get("/analysis/binance/formations?symbol=UNKNOWNUSDT").status_code == 404
    assert client.get("/analysis/binance/formations?symbol=BTC/USDT").status_code == 422
