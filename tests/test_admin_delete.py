from uuid import uuid4

import pytest
from test_admin_accounts import admin_client as account_setup
from test_private_purchases import STAMP, auth, record

from app.db.models.account import Account, AccountSession, Purchase
from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.candidate_observation import CandidateObservation
from app.db.models.candidate_outcome import CandidateOutcome
from app.db.models.candidate_scan import CandidateScan
from app.db.models.portfolio_observation import PortfolioObservation
from app.db.models.portfolio_snapshot import PortfolioSnapshot
from app.db.session import SessionLocal


@pytest.fixture
def admin_client(monkeypatch):
    yield from account_setup.__wrapped__(monkeypatch)


def remove(client, username, headers=None, confirmation=None):
    return client.request(
        "DELETE",
        "/account/admin/users/" + username,
        headers=headers or {},
        json=dict(confirm_username=confirmation or username),
    )


def test_admin_only_confirmation_and_protected_administrator(admin_client):
    assert remove(admin_client, "bob").status_code == 401
    headers = auth(admin_client)
    assert remove(admin_client, "bob", headers).status_code == 403
    headers = auth(admin_client, "bilalozok")
    assert remove(admin_client, "bob").status_code == 403
    assert (
        remove(admin_client, "bob", {**headers, "Origin": "https://evil.example"}).status_code
        == 403
    )
    assert remove(admin_client, "bob", headers, "alice").status_code == 422
    assert remove(admin_client, "bilalozok", headers).status_code == 403
    assert remove(admin_client, "missing", headers).status_code == 404


def test_delete_personal_data_and_sessions_keeps_other_users_and_market(admin_client):
    headers = auth(admin_client, "bob")
    assert (
        admin_client.post("/account/purchases", json=record(), headers=headers).status_code == 201
    )
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="bob").one().id
        scan = str(uuid4())
        db.add(
            CandidateScan(
                id=scan,
                account_id=owner,
                request_id=str(uuid4()),
                created_ms=STAMP,
                rule_hash="a" * 64,
                payload=dict(candidates=[]),
            )
        )
        db.flush()
        db.add(CandidateObservation(scan_id=scan, symbol="BTCUSDT", close_ms=STAMP, payload={}))
        db.add(CandidateOutcome(scan_id=scan, symbol="BTCUSDT", horizon_bars=4, payload={}))
        db.add(
            PortfolioObservation(
                id=str(uuid4()),
                account_id=owner,
                symbol="BTCUSDT",
                local_day="2026-10-05",
                observed_ms=STAMP,
                method="test",
                payload={},
            )
        )
        db.add(
            PortfolioSnapshot(
                id=str(uuid4()),
                account_id=owner,
                symbol="BTCUSDT",
                request_id=str(uuid4()),
                observed_ms=STAMP,
                method="test",
                payload={},
                comparison={},
            )
        )
        db.commit()
    headers = auth(admin_client, "bilalozok")
    response = remove(admin_client, "bob", headers)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert {u.username for u in db.query(Account)} == {"alice", "bilalozok"}
        assert db.query(AccountSession).filter_by(account_id=owner).count() == 0
        for model in (Purchase, CandidateScan, PortfolioObservation, PortfolioSnapshot):
            assert db.query(model).filter_by(account_id=owner).count() == 0
        for model in (CandidateObservation, CandidateOutcome):
            assert db.query(model).filter_by(scan_id=scan).count() == 0
        assert db.query(BinanceSpotSymbol).count() == 1
