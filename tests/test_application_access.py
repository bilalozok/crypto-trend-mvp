import pytest
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as account_setup

from app.db.models.account import Account, AccountSession
from app.db.session import SessionLocal


@pytest.fixture
def private_client(monkeypatch):
    yield from account_setup.__wrapped__(monkeypatch)


def test_all_data_routes_block_anonymous_and_pages_redirect(private_client):
    for path in (
        "/market/binance/symbols",
        "/candles/latest",
        "/signals/trend",
        "/analysis/binance/chart-data",
        "/analysis/binance/formations/scan",
        "/analysis/binance/early-formations",
        "/analysis/binance/forward/reports",
        "/openapi.json",
        "/account/dashboard",
    ):
        response = private_client.get(path, follow_redirects=False)
        assert response.status_code == 401, (path, response.text)
        assert response.headers["cache-control"] == "no-store"
    for path in ("/", "/analysis/binance/dashboard", "/analysis/binance/chart", "/docs"):
        response = private_client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=")
    assert private_client.get("/login").status_code == 200
    assert private_client.get("/health").json() == {"status": "ok"}
    assert private_client.post("/candles/fetch/BTCUSDT").status_code == 401


def test_login_root_csrf_revocation_and_inactive_accounts(private_client, monkeypatch):
    headers = auth(private_client)
    response = private_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/analysis/binance/dashboard?tab=overview"
    assert private_client.get("/analysis/binance/dashboard").status_code == 200
    assert private_client.get("/openapi.json").status_code == 200
    assert private_client.post("/analysis/binance/forward/reports").status_code == 403
    # With CSRF, authenticated report creation is allowed.
    assert (
        private_client.post("/analysis/binance/forward/reports", headers=headers).status_code == 201
    )
    assert (
        private_client.post(
            "/analysis/binance/forward/reports",
            headers={**headers, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    with SessionLocal() as db:
        db.query(AccountSession).update({AccountSession.expires_ms: STAMP})
        db.commit()
    assert private_client.get("/market/binance/symbols").status_code == 401
    auth(private_client)
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one()
        owner.active = False
        db.commit()
    assert private_client.get("/market/binance/symbols").status_code == 401
