from uuid import uuid4

import pytest
from test_private_purchases import STAMP, auth, record
from test_private_purchases import private_client as account_setup

from app.db.models.account import Account
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services import formation_early, personal_dashboard  # noqa: F401


@pytest.fixture
def private_client(monkeypatch):
    yield from account_setup.__wrapped__(monkeypatch)


def test_dashboard_requires_login_and_isolates_portfolio_scans_and_alerts(
    private_client, monkeypatch
):
    assert private_client.get("/account/dashboard").status_code == 401
    assert private_client.get("/account/dashboard/early").status_code == 401
    monkeypatch.setattr(
        formation_early,
        "listing",
        lambda db, stamp: dict(
            checked_at=stamp,
            alerts=[
                dict(symbol=s, direction="up", expires_ms=stamp + 1000)
                for s in ("BTCUSDT", "ETHUSDT", "OTHERUSDT")
            ],
        ),
    )
    auth(private_client, "bob")
    with SessionLocal() as db:
        bob = db.query(Account).filter_by(username="bob").one()
        scan = CandidateScan(
            id=str(uuid4()),
            account_id=bob.id,
            request_id=str(uuid4()),
            created_ms=STAMP,
            rule_hash="a" * 64,
            payload=dict(candidates=[dict(symbol="ETHUSDT")]),
        )
        db.add(scan)
        db.commit()
        bob_scan = scan.id
    response = private_client.get("/account/dashboard")
    assert response.status_code == 200, response.text
    assert response.json()["scan_id"] == bob_scan
    auth(private_client)
    data = private_client.get("/account/dashboard").json()
    assert data["scan_id"] is None and data["alerts"] == []
    assert data["portfolio_total"] == 0
    headers = auth(private_client)
    assert (
        private_client.post(
            "/account/purchases", json=record(currency="USDT"), headers=headers
        ).status_code
        == 201
    )
    response = private_client.get("/account/dashboard")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["portfolio_total"] == 1 and data["portfolio"][0]["symbol"] == "BTCUSDT"
    assert {a["symbol"] for a in data["alerts"]} == {"BTCUSDT"}
    assert len(data["attention"]) == 3
    assert private_client.get("/account/dashboard?offset=10").json()["portfolio"] == []
    assert private_client.get("/account/dashboard?offset=-1").status_code == 422
    early = private_client.get("/account/dashboard/early")
    assert early.status_code == 200 and early.headers["cache-control"] == "no-store"
    assert {a["symbol"] for a in early.json()["alerts"]} == {"BTCUSDT"}


def test_early_poll_does_not_recalculate_portfolio(private_client, monkeypatch):
    auth(private_client)
    monkeypatch.setattr(
        "app.services.personal_dashboard.portfolio_technical.technical",
        lambda *args: pytest.fail("Polling must not recalculate formations"),
    )
    assert private_client.get("/account/dashboard/early").status_code == 200
