from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_private_purchases import STAMP, auth, record
from test_private_purchases import private_client as private_setup

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.portfolio_observation import PortfolioObservation
from app.db.session import SessionLocal
from app.services import portfolio_technical as technical


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def buy(client, headers):
    assert client.post("/account/purchases", json=record(), headers=headers).status_code == 201


def seed(count=200, gap=False, stale=False):
    with SessionLocal() as db:
        for i in range(count):
            if gap and i == 50:
                continue
            opened = STAMP - (count - i) * 900000 - (900000 if stale else 0)
            db.add(
                BinanceSpotCandle(
                    symbol="BTCUSDT",
                    interval="15m",
                    open_time=opened,
                    open=10,
                    high=11,
                    low=9,
                    close=10,
                    volume=1,
                )
            )
        db.commit()


def test_owner_auth_and_csrf(private_client):
    assert private_client.get("/account/portfolio").status_code == 401
    headers = auth(private_client)
    buy(private_client, headers)
    assert (
        private_client.post(
            "/account/portfolio/observations", json={"symbol": "BTCUSDT"}
        ).status_code
        == 403
    )
    auth(private_client, "bob")
    assert private_client.get("/account/portfolio").json()["coins"] == []
    assert private_client.get("/account/portfolio/history?symbol=BTCUSDT").status_code == 404
    headers = {"X-CSRF-Token": private_client.get("/account/session").json()["csrf_token"]}
    assert (
        private_client.post(
            "/account/portfolio/observations", json={"symbol": "BTCUSDT"}, headers=headers
        ).status_code
        == 404
    )
    assert private_client.get("/account/portfolio").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("options", [dict(count=199), dict(gap=True), dict(stale=True)])
def test_unready_is_not_saved(private_client, options):
    headers = auth(private_client)
    buy(private_client, headers)
    seed(**options)
    result = private_client.post(
        "/account/portfolio/observations", json={"symbol": "BTCUSDT"}, headers=headers
    ).json()
    assert result["reason"] == "not_ready"
    assert result["current"]["assessment"] == "unavailable"
    assert (
        private_client.get("/account/portfolio/history?symbol=BTCUSDT").json()["observations"] == []
    )


def test_daily_snapshot_immutable_private_and_distinct_coin(private_client, monkeypatch):
    headers = auth(private_client)
    buy(private_client, headers)
    buy(private_client, headers)
    seed()
    data = private_client.get("/account/portfolio").json()
    assert len(data["coins"]) == 1
    assert data["coins"][0]["assessment"] == "waiting"
    assert [h["status"] for h in data["coins"][0]["horizons"]][1:] == [
        "not_supported",
        "not_supported",
    ]
    first = private_client.post(
        "/account/portfolio/observations", json={"symbol": "BTCUSDT"}, headers=headers
    ).json()
    assert first["created"] is True
    with SessionLocal() as db:
        row = (
            db.query(BinanceSpotCandle)
            .filter_by(symbol="BTCUSDT", open_time=STAMP - 900000)
            .first()
        )
        row.close = 10.5
        db.commit()
    second = private_client.post(
        "/account/portfolio/observations", json={"symbol": "BTCUSDT"}, headers=headers
    ).json()
    assert second["created"] is False
    assert second["observation"] == first["observation"]
    with SessionLocal() as db:
        assert db.query(PortfolioObservation).count() == 1
    history = private_client.get("/account/portfolio/history?symbol=BTCUSDT").json()
    assert history["observations"][0]["technical"]["last_close"] == "10.0"
    assert "quantity" not in history["observations"][0]["technical"]


def test_future_candle_excluded(private_client):
    auth(private_client)
    seed()
    with SessionLocal() as db:
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                interval="15m",
                open_time=STAMP,
                open=100,
                high=101,
                low=99,
                close=100,
                volume=100,
            )
        )
        db.commit()
        data = technical.technical(db, "BTCUSDT", STAMP)
    assert data["last_close"] == "10.0"
    assert data["candle_close_time"] == datetime.fromtimestamp(STAMP / 1000, UTC).isoformat()


def test_turkey_day_not_utc_day_and_read_only_refresh(private_client, monkeypatch):
    headers = auth(private_client)
    buy(private_client, headers)
    seed()
    monkeypatch.setattr(
        technical,
        "technical",
        lambda db, symbol, stamp: dict(
            status="ready", assessment="waiting", label="Teyit bekleniyor"
        ),
    )
    late = round(datetime(2026, 10, 5, 22, 0, tzinfo=UTC).timestamp() * 1000)
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").first().id
        result = technical.save_daily(db, owner, "BTCUSDT", late)
    assert result["observation"]["day"] == "2026-10-06"


def test_evidence_and_condition_explanations(monkeypatch):
    pattern = dict(
        name="Çift dip",
        current_confirmation=True,
        direction="up",
        report_volume_supported=False,
        status="confirmed",
    )
    monkeypatch.setattr(
        technical,
        "build_report",
        lambda _: dict(patterns=[pattern], assessment="bullish_setup", counts={}),
    )

    class DB:
        def get(self, *_):
            return SimpleNamespace(active=True)

        def scalars(self, *_):
            return SimpleNamespace(all=lambda: [])

    result = technical.technical(DB(), "BTCUSDT", STAMP)
    assert "yükseliş" in result["positive_notes"][0]
    assert "hacmi" in result["negative_notes"][0]
    assert "geçersizlik" in result["guidance"]
