import pytest
from test_private_purchases import PASSWORD, STAMP, auth
from test_private_purchases import private_client as account_setup

from app.db.models.account import Account
from app.db.session import SessionLocal
from app.services import account_auth


@pytest.fixture
def admin_client(monkeypatch):
    for client in account_setup.__wrapped__(monkeypatch):
        with SessionLocal() as db:
            account_auth.create_account(db, "bilalozok", PASSWORD, STAMP)
        yield client


def test_admin_permissions_csrf_and_private_list(admin_client):
    payload = dict(username="new_user", password=PASSWORD)
    assert admin_client.get("/account/admin/users").status_code == 401
    assert admin_client.post("/account/admin/users", json=payload).status_code == 401
    headers = auth(admin_client)
    assert admin_client.get("/account/session").json()["can_manage_users"] is False
    assert admin_client.get("/account/admin/users").status_code == 403
    assert (
        admin_client.post("/account/admin/users", json=payload, headers=headers).status_code == 403
    )
    headers = auth(admin_client, "bilalozok")
    assert admin_client.get("/account/session").json()["can_manage_users"] is True
    response = admin_client.get("/account/admin/users")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert {u["username"] for u in response.json()["users"]} == {"alice", "bob", "bilalozok"}
    assert "password" not in response.text
    assert admin_client.post("/account/admin/users", json=payload).status_code == 403
    assert (
        admin_client.post(
            "/account/admin/users",
            json=payload,
            headers={**headers, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )


def test_created_account_login_isolation_and_no_escalation(admin_client):
    headers = auth(admin_client, "bilalozok")
    payload = dict(username="NEW_USER", password=PASSWORD)
    response = admin_client.post("/account/admin/users", json=payload, headers=headers)
    assert response.status_code == 201
    assert response.json() == dict(username="new_user", active=True)
    assert (
        admin_client.post("/account/admin/users", json=payload, headers=headers).status_code == 409
    )
    with SessionLocal() as db:
        user = db.query(Account).filter_by(username="new_user").one()
        assert user.password_hash != PASSWORD
        assert account_auth.verify_password(PASSWORD, user.password_hash)
    auth(admin_client, "new_user")
    assert admin_client.get("/account/session").json()["can_manage_users"] is False
    assert admin_client.get("/account/admin/users").status_code == 403
    assert admin_client.get("/account/purchases").json()["purchases"] == []


def test_validation_and_inactive_admin(admin_client):
    headers = auth(admin_client, "bilalozok")
    for data in [
        dict(username="user", password="short"),
        dict(username="bad name", password=PASSWORD),
        dict(username="user", password=PASSWORD, is_admin=True),
    ]:
        assert (
            admin_client.post("/account/admin/users", json=data, headers=headers).status_code == 422
        )
    assert (
        admin_client.post(
            "/account/admin/users",
            json=dict(username="BILALOZOK", password=PASSWORD),
            headers=headers,
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        assert db.query(Account).count() == 3
        admin = db.query(Account).filter_by(username="bilalozok").one()
        admin.active = False
        db.commit()
    assert admin_client.get("/account/admin/users").status_code == 401


def test_password_reset_revokes_all_target_sessions_and_preserves_other_users(admin_client):
    with SessionLocal() as db:
        first = account_auth.login(db, "alice", PASSWORD, STAMP)
        second = account_auth.login(db, "alice", PASSWORD, STAMP)
        bob_token = account_auth.login(db, "bob", PASSWORD, STAMP)
    headers = auth(admin_client, "bilalozok")
    new_password = "replacement-password-1234"
    response = admin_client.post(
        "/account/admin/users/password",
        json=dict(username="ALICE", password=new_password),
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == dict(username="alice", password_changed=True, reauthenticate=False)
    assert new_password not in response.text
    for token in (first, second):
        assert (
            admin_client.get(
                "/account/session", headers={"Cookie": account_auth.COOKIE + "=" + token}
            ).status_code
            == 401
        )
    assert (
        admin_client.get(
            "/account/session", headers={"Cookie": account_auth.COOKIE + "=" + bob_token}
        ).status_code
        == 200
    )
    assert (
        admin_client.post(
            "/account/login", json=dict(username="alice", password=PASSWORD)
        ).status_code
        == 401
    )
    assert (
        admin_client.post(
            "/account/login", json=dict(username="alice", password=new_password)
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        row = db.query(Account).filter_by(username="alice").one()
        assert row.active is True
        assert account_auth.verify_password(new_password, row.password_hash)


def test_password_reset_requires_admin_csrf_origin_and_existing_target(admin_client):
    payload = dict(username="bob", password="replacement-password-1234")
    assert admin_client.post("/account/admin/users/password", json=payload).status_code == 401
    headers = auth(admin_client)
    assert (
        admin_client.post(
            "/account/admin/users/password", json=payload, headers=headers
        ).status_code
        == 403
    )
    headers = auth(admin_client, "bilalozok")
    assert admin_client.post("/account/admin/users/password", json=payload).status_code == 403
    assert (
        admin_client.post(
            "/account/admin/users/password",
            json=payload,
            headers={**headers, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        admin_client.post(
            "/account/admin/users/password",
            json=dict(username="bob", password="short"),
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        admin_client.post(
            "/account/admin/users/password",
            json=dict(username="missing", password=PASSWORD),
            headers=headers,
        ).status_code
        == 404
    )
    with SessionLocal() as db:
        assert db.query(Account).count() == 3
        assert account_auth.verify_password(
            PASSWORD, db.query(Account).filter_by(username="bob").one().password_hash
        )


def test_admin_own_password_change_requires_reauthentication(admin_client):
    headers = auth(admin_client, "bilalozok")
    password = "admin-new-password-1234"
    response = admin_client.post(
        "/account/admin/users/password",
        json=dict(username="bilalozok", password=password),
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["reauthenticate"] is True
    assert admin_client.get("/account/admin/users").status_code == 401
    assert (
        admin_client.post(
            "/account/login", json=dict(username="bilalozok", password=password)
        ).status_code
        == 200
    )
    assert admin_client.get("/account/session").json()["can_manage_users"] is True
