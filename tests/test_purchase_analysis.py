from decimal import Decimal

import pytest
from test_private_purchases import STAMP, auth, record
from test_private_purchases import private_client as purchase_client_setup

from app.db.models.binance_spot import BinanceSpotCandle
from app.db.session import SessionLocal
from app.services import purchase_analysis as analysis


@pytest.fixture
def private_client(monkeypatch):
    yield from purchase_client_setup.__wrapped__(monkeypatch)


def seed(opened=None, price=12, low=9, high=13, open_price=10):
    with SessionLocal() as db:
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                open_time=STAMP - analysis.BAR if opened is None else opened,
                interval="15m",
                open=open_price,
                high=high,
                low=low,
                close=price,
                volume=1,
            )
        )
        db.commit()


def add(client, headers, **values):
    response = client.post("/account/purchases", json=record(**values), headers=headers)
    assert response.status_code == 201


def test_private_analysis_requires_auth_and_owner(private_client, monkeypatch):
    for path in ("valuation", "price-range?symbol=BTCUSDT"):
        response = private_client.get("/account/purchases/" + path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    headers = auth(private_client)
    add(private_client, headers)
    auth(private_client, "bob")
    assert private_client.get("/account/purchases/valuation").json()["groups"] == []
    assert private_client.get("/account/purchases/price-range?symbol=BTCUSDT").status_code == 404


def test_exact_cost_currency_conversion_and_date_filter(private_client, monkeypatch):
    headers = auth(private_client)
    add(private_client, headers, currency="USDT", unit_price="10", quantity="2", fee="1")
    seed()
    assert (
        private_client.post(
            "/account/purchases", json=record(currency="TRY"), headers=headers
        ).status_code
        == 422
    )
    data = private_client.get("/account/purchases/valuation").json()
    groups = {r["currency"]: r for r in data["groups"]}
    assert Decimal(groups["USDT"]["total_cost"]) == 21
    assert Decimal(groups["USDT"]["market_value"]) == 24
    assert Decimal(groups["USDT"]["unrealized_return"]) == 3
    assert len(data["totals"]) == 1
    assert (
        private_client.get("/account/purchases/valuation?end=2026-10-05T15:00:00Z").json()["groups"]
        == []
    )


@pytest.mark.parametrize("missing", ["coin", "new_purchase", "bad_ohlc"])
def test_unavailable_is_not_zero_or_partial_total(private_client, monkeypatch, missing):
    headers = auth(private_client)
    date = "2026-10-05T19:00:00.001Z" if missing == "new_purchase" else "2026-10-05T15:00:00Z"
    if missing == "new_purchase":
        monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 1000)
    add(private_client, headers, purchased_at=date)
    if missing != "coin":
        seed(high=11 if missing == "bad_ohlc" else 13)
    data = private_client.get("/account/purchases/valuation").json()
    assert data["groups"][0]["status"] == "unavailable"
    assert data["groups"][0]["market_value"] is None
    assert data["totals"][0]["market_value"] is None
    assert data["totals"][0]["unavailable_groups"] == 1


def test_closed_range_gap_exclusion_and_purchase_markers(private_client):
    headers = auth(private_client)
    add(private_client, headers, purchased_at="2026-10-05T18:40:00Z", currency="USDT")
    seed(STAMP - 2 * analysis.BAR, price=11)
    seed(price=12)
    # A still-open candle must not leak into the range.
    seed(STAMP, price=99, high=100)
    url = (
        "/account/purchases/price-range?symbol=BTCUSDT"
        "&start=2026-10-05T18:30:00Z&end=2026-10-05T19:00:00Z"
    )
    data = private_client.get(url).json()
    assert data["status"] == "ready" and data["expected_candles"] == 2
    assert Decimal(data["change_pct"]) == 20
    assert len(data["points"]) == 2 and len(data["purchases"]) == 1
    assert Decimal(data["highest_price"]) == 13
    data = private_client.get(url.replace("18:30:00", "18:15:00")).json()
    assert data["status"] == "incomplete" and data["missing_candles"] == 1
    assert data["change_pct"] is None
    assert (
        private_client.get(url.replace("2026-10-05T18:30:00", "2026-08-01T18:30:00")).status_code
        == 422
    )
    assert private_client.get(url.replace("19:00:00", "20:00:00")).status_code == 422


def test_partial_bar_start_excluded(private_client):
    auth(private_client)
    with SessionLocal() as db:
        seed(STAMP - 2 * analysis.BAR, price=11)
        seed()
        result = analysis.price_range(db, "BTCUSDT", STAMP - 2 * analysis.BAR + 1, STAMP, STAMP)
    assert result["expected_candles"] == 1
    assert result["candles_used"] == 1
    assert result["status"] == "incomplete"


def test_tiny_amounts_preserve_value_and_chart_decimal_strings(private_client, monkeypatch):
    headers = auth(private_client)
    add(
        private_client,
        headers,
        currency="USDT",
        unit_price="0.000000000000000001",
        quantity="0.000000000000000001",
        fee="0",
    )
    seed(price=1e-7, low=8e-8, high=13e-8, open_price=1e-7)
    seed(STAMP - 2 * analysis.BAR, price=1e-7, low=8e-8, high=13e-8, open_price=1e-7)
    data = private_client.get("/account/purchases/valuation").json()
    assert Decimal(data["totals"][0]["total_cost"]) == Decimal("1e-36")
    assert Decimal(data["groups"][0]["market_value"]) == Decimal("1e-25")
    with SessionLocal() as db:
        result = analysis.price_range(db, "BTCUSDT", STAMP - 2 * analysis.BAR, STAMP, STAMP)
    assert result["last_close"] == "0.0000001"
    assert result["points"][0]["price"] == "0.0000001"


def test_legacy_currency_preserved_but_excluded(private_client):
    headers = auth(private_client)
    add(private_client, headers)
    with SessionLocal() as db:
        from sqlalchemy import select

        from app.db.models.account import Purchase

        row = db.scalar(select(Purchase))
        row.currency = "TRY"
        db.commit()
    data = private_client.get("/account/purchases/valuation").json()
    assert data["groups"] == [] and data["totals"] == []
    assert data["excluded_non_usdt_purchases"] == 1
    summary = private_client.get("/account/purchases/summary").json()
    assert summary["groups"] == [] and summary["excluded_non_usdt_purchases"] == 1
    assert private_client.get("/account/purchases").json()["purchases"][0]["currency"] == "TRY"
