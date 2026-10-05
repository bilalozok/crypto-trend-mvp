from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.models.account import Account, AccountSession, Purchase
from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.session import SessionLocal
from app.main import app
from app.services import account_auth

STAMP = round(datetime(2026, 10, 5, 19, 0, tzinfo=UTC).timestamp() * 1000)
PASSWORD = "this-is-a-test-password"


@pytest.fixture
def private_client(monkeypatch):
    monkeypatch.setenv("PRIVATE_APP_ORIGIN", "https://testserver")
    monkeypatch.setattr(account_auth, "SCRYPT_N", 2**11)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP)
    with SessionLocal() as db:
        account_auth.create_account(db, "alice", PASSWORD, STAMP)
        account_auth.create_account(db, "bob", PASSWORD, STAMP)
        db.add(
            BinanceSpotSymbol(
                symbol="BTCUSDT",
                base_asset="BTC",
                active=True,
                quote_volume_24h=1,
                catalog_updated_ms=0,
            )
        )
        db.commit()
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def auth(client, username="alice"):
    response = client.post("/account/login", json=dict(username=username, password=PASSWORD))
    assert response.status_code == 200
    data = client.get("/account/session").json()
    return {"X-CSRF-Token": data["csrf_token"]}


def record(**values):
    result = dict(
        id=str(uuid4()),
        symbol="BTCUSDT",
        purchased_at="2026-10-05T18:00:00+03:00",
        currency="TRY",
        unit_price="0.1",
        quantity="0.2",
        fee="0.001",
        note="my note",
    )
    result.update(values)
    return result


def test_real_password_hash_and_constant_format():
    encoded = account_auth.password_hash(PASSWORD)
    assert encoded != PASSWORD and len(encoded) < 192
    assert account_auth.verify_password(PASSWORD, encoded)
    assert not account_auth.verify_password("wrong", encoded)
    assert not account_auth.verify_password(PASSWORD, "bad")


def test_private_routes_require_login_and_no_cache(private_client):
    for path in ("/account/session", "/account/purchases", "/account/purchases/summary"):
        response = private_client.get(path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    assert private_client.post("/account/purchases", json=record()).status_code == 401


def test_secure_cookie_csrf_origin_logout_and_expiry(private_client, monkeypatch):
    headers = auth(private_client)
    cookie = private_client.cookies.get(account_auth.COOKIE)
    with SessionLocal() as db:
        stored = db.query(AccountSession).one()
        assert stored.token_hash != cookie
    assert private_client.post("/account/purchases", json=record()).status_code == 403
    assert (
        private_client.post(
            "/account/purchases",
            json=record(),
            headers={**headers, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    response = private_client.post("/account/login", json=dict(username="alice", password=PASSWORD))
    cookie_header = response.headers["set-cookie"].lower()
    assert (
        "secure" in cookie_header
        and "httponly" in cookie_header
        and "samesite=strict" in cookie_header
    )
    headers = {"X-CSRF-Token": private_client.get("/account/session").json()["csrf_token"]}
    assert private_client.post("/account/logout", headers=headers).status_code == 200
    assert private_client.get("/account/purchases").status_code == 401
    auth(private_client)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + account_auth.SESSION_MS + 1)
    assert private_client.get("/account/purchases").status_code == 401


def test_exact_decimals_currency_groups_and_idempotent_save(private_client):
    headers = auth(private_client)
    data = record()
    response = private_client.post("/account/purchases", json=data, headers=headers)
    assert response.status_code == 201 and response.json()["total_cost"] == "0.021"
    assert private_client.post("/account/purchases", json=data, headers=headers).status_code == 201
    assert (
        private_client.post(
            "/account/purchases", json={**data, "quantity": "2"}, headers=headers
        ).status_code
        == 409
    )
    second = record(currency="USDT", unit_price="10", quantity="2", fee="1")
    assert (
        private_client.post("/account/purchases", json=second, headers=headers).status_code == 201
    )
    groups = private_client.get("/account/purchases/summary").json()["groups"]
    assert {g["currency"] for g in groups} == {"TRY", "USDT"}
    by_currency = {g["currency"]: g for g in groups}
    assert by_currency["TRY"]["average_cost"] == "0.105000000000000000"
    assert by_currency["USDT"]["average_cost"] == "10.500000000000000000"
    assert len(private_client.get("/account/purchases").json()["purchases"]) == 2


def test_another_account_cannot_read_or_delete_purchase(private_client):
    first = record()
    headers = auth(private_client)
    private_client.post("/account/purchases", json=first, headers=headers)
    other = auth(private_client, "bob")
    assert private_client.get("/account/purchases").json()["purchases"] == []
    assert private_client.get("/account/purchases/summary").json()["groups"] == []
    assert (
        private_client.delete("/account/purchases/" + first["id"], headers=other).status_code == 404
    )
    assert private_client.post("/account/purchases", json=first, headers=other).status_code == 409
    with SessionLocal() as db:
        assert db.query(Purchase).count() == 1


@pytest.mark.parametrize(
    "change",
    [
        dict(unit_price="NaN"),
        dict(quantity="-1"),
        dict(fee="-1"),
        dict(currency="USD"),
        dict(purchased_at="2026-10-06T10:00:00+03:00"),
        dict(purchased_at="2026-10-05T10:00:00"),
        dict(symbol="UNKNOWNUSDT"),
        dict(account_id="other"),
        dict(quantity="0.0000000000000000001"),
    ],
)
def test_purchase_validation(private_client, change):
    headers = auth(private_client)
    assert (
        private_client.post(
            "/account/purchases", json=record(**change), headers=headers
        ).status_code
        == 422
    )


def test_date_range_end_exclusive_and_pagination(private_client):
    headers = auth(private_client)
    for hour in (17, 18, 19):
        private_client.post(
            "/account/purchases",
            json=record(purchased_at=f"2026-10-05T{hour}:00:00+03:00"),
            headers=headers,
        )
    data = private_client.get(
        "/account/purchases",
        params=dict(start="2026-10-05T17:00:00+03:00", end="2026-10-05T19:00:00+03:00", limit=1),
    ).json()
    assert len(data["purchases"]) == 1 and data["next_offset"] == 1
    summary = private_client.get(
        "/account/purchases/summary",
        params=dict(start="2026-10-05T17:00:00+03:00", end="2026-10-05T19:00:00+03:00"),
    ).json()
    assert summary["groups"][0]["purchases"] == 2


def test_login_limit_and_generic_failure(private_client):
    for _ in range(6):
        response = private_client.post(
            "/account/login", json=dict(username="unknown", password="wrong")
        )
        assert (
            response.status_code == 401
            and response.json()["detail"] == "Kullanıcı adı veya parola yanlış."
        )
    assert (
        private_client.post(
            "/account/login", json=dict(username="unknown", password="wrong")
        ).status_code
        == 429
    )


def test_password_reset_revokes_sessions(private_client):
    auth(private_client)
    with SessionLocal() as db:
        account_auth.create_account(db, "alice", "another-test-password", STAMP, reset=True)
        assert db.query(AccountSession).count() == 0
        assert db.query(Account).count() == 2
    assert private_client.get("/account/purchases").status_code == 401


def test_owner_delete_requires_csrf_and_removes_only_own_row(private_client):
    headers = auth(private_client)
    data = record()
    private_client.post("/account/purchases", json=data, headers=headers)
    route = "/account/purchases/" + data["id"]
    assert private_client.delete(route).status_code == 403
    assert private_client.delete(route, headers=headers).status_code == 200
    assert private_client.get("/account/purchases").json()["purchases"] == []
