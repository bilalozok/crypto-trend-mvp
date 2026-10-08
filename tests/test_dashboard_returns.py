from decimal import Decimal
from uuid import uuid4

import pytest
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as account_setup

from app.db.models.account import Account, Purchase
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.session import SessionLocal
from app.services.dashboard_returns import DAY, summary
from app.services.formations import BAR


@pytest.fixture
def private_client(monkeypatch):
    yield from account_setup.__wrapped__(monkeypatch)


def buy(db, owner, at, price=10, fee=1, currency="USDT", symbol="BTCUSDT"):
    db.add(
        Purchase(
            id=str(uuid4()),
            account_id=owner,
            symbol=symbol,
            purchased_ms=at,
            unit_price=str(price),
            quantity="2",
            fee=str(fee),
            currency=currency,
            note="",
            created_ms=STAMP,
        )
    )


def candle(db, at, price):
    db.add(
        BinanceSpotCandle(
            symbol="BTCUSDT",
            open_time=at - BAR,
            interval="15m",
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1,
        )
    )


def test_owner_cost_new_lots_and_exact_period_comparison(private_client):
    with SessionLocal() as db:
        alice = db.query(Account).filter_by(username="alice").one().id
        bob = db.query(Account).filter_by(username="bob").one().id
        buy(db, alice, STAMP - 8 * DAY)
        buy(db, alice, STAMP - DAY // 2, price=12)
        buy(db, alice, STAMP - 8 * DAY, currency="TRY")
        buy(db, bob, STAMP - 8 * DAY, price=100)
        for days, price in ((0, 15), (1, 11), (7, 9)):
            candle(db, STAMP - days * DAY, price)
        db.commit()
        data = summary(db, alice, STAMP)
        coin = data["coins"][0]
        assert Decimal(coin["cost"]) == 46
        assert Decimal(coin["value"]) == 60
        assert Decimal(coin["profit"]) == 14
        assert Decimal(coin["day_change"]) == 13  # Prior profit 22-21=1
        assert Decimal(coin["week_change"]) == 17  # Prior profit 18-21=-3
        assert data["excluded_try_purchases"] == 1
        assert data["history"][1]["profit"] is None
        assert db.query(Purchase).count() == 4
    assert private_client.get("/account/dashboard/returns").status_code == 401
    auth(private_client)
    response = private_client.get("/account/dashboard/returns")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert Decimal(response.json()["totals"]["profit"]) == 14
    auth(private_client, "bob")
    assert (
        Decimal(private_client.get("/account/dashboard/returns").json()["totals"]["profit"]) == -171
    )


def test_missing_current_and_baseline_are_not_zero(private_client):
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        buy(db, owner, STAMP - 8 * DAY)
        candle(db, STAMP - BAR, 15)  # old candle cannot substitute current closing price
        db.commit()
        data = summary(db, owner, STAMP)
        assert data["totals"]["profit"] is None and data["missing_current"] == 1
        candle(db, STAMP, 15)
        db.commit()
        data = summary(db, owner, STAMP)
        assert data["totals"]["profit"] == "9.0"
        assert data["totals"]["day_change"] is None
        assert data["totals"]["week_change"] is None


def test_no_holdings_before_purchase_do_not_need_price(private_client):
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        buy(db, owner, STAMP - DAY // 2)
        buy(db, owner, STAMP + BAR)  # future purchase is excluded
        candle(db, STAMP, 15)
        db.commit()
        data = summary(db, owner, STAMP)
        assert data["coins"][0]["quantity"] == "2"
        assert data["totals"]["day_change"] == data["totals"]["profit"] == "9.0"
        assert data["history"][0]["profit"] == "0"
