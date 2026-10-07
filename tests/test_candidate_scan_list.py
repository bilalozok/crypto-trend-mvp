from uuid import uuid4

import pytest
from test_candidate_archive import payload
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as account_setup

from app.db.models.account import Account
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services.candidate_scan_list import listing
from app.services.formations import BAR, timestamp


@pytest.fixture
def private_client(monkeypatch):
    yield from account_setup.__wrapped__(monkeypatch)


def add(db, owner, created, entry, symbol="BTCUSDT"):
    data = payload()
    data["candidates"] = [dict(symbol=symbol)]
    data["evaluation_entry_ms"] = entry
    row = CandidateScan(
        id=str(uuid4()),
        account_id=owner,
        request_id=str(uuid4()),
        created_ms=created,
        rule_hash="test",
        payload=data,
    )
    db.add(row)
    return row


def test_filters_before_pagination_and_tracking_boundaries(private_client):
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        other = db.query(Account).filter_by(username="bob").one().id
        for n in range(25):
            add(db, owner, STAMP + n, STAMP, "ETHUSDT")
        legacy = add(db, owner, STAMP - 1, None)
        open_row = add(db, owner, STAMP - 2, STAMP)
        ended = add(db, owner, STAMP - 3, STAMP - 97 * BAR)
        future = add(db, owner, STAMP - 4, STAMP + BAR)
        add(db, other, STAMP + 99, STAMP, "ETHUSDT")
        db.commit()
        first = listing(db, owner, STAMP, symbol="ethusdt")
        assert len(first["scans"]) == 20 and first["next_offset"] == 20
        assert len(listing(db, owner, STAMP, offset=20, symbol="ETHUSDT")["scans"]) == 5
        assert len(listing(db, owner, STAMP, symbol="BTCUSDT")["scans"]) == 4
        assert listing(db, owner, STAMP, kind="legacy")["scans"][0]["id"] == legacy.id
        assert listing(db, owner, STAMP, kind="ended")["scans"][0]["id"] == ended.id
        assert (
            listing(db, owner, STAMP, kind="open", symbol="BTCUSDT")["scans"][0]["id"]
            == open_row.id
        )
        boundary = listing(db, owner, STAMP + 96 * BAR, kind="open", symbol="BTCUSDT")
        assert {r["id"] for r in boundary["scans"]} == {open_row.id, future.id}
        assert (
            listing(db, owner, STAMP + 96 * BAR + 1, kind="open", symbol="BTCUSDT")["scans"][0][
                "id"
            ]
            == future.id
        )
        ranged = listing(db, owner, STAMP, start=STAMP - 2, end=STAMP)
        assert {r["id"] for r in ranged["scans"]} == {legacy.id, open_row.id}


def test_list_api_validation_owner_and_no_writes(private_client, monkeypatch):
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP)
    path = "/account/candidate-scans"
    assert private_client.get(path).status_code == 401
    auth(private_client)
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        row = add(db, owner, STAMP, STAMP)
        db.commit()
        row_id = row.id
    response = private_client.get(
        path, params=dict(kind="open", symbol="btcusdt", start=timestamp(STAMP).isoformat())
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["scans"][0]["id"] == row_id
    for params in [
        dict(kind="unknown"),
        dict(symbol="BTC&x"),
        dict(start="2026-10-07T10:00:00"),
        dict(start=timestamp(STAMP).isoformat(), end=timestamp(STAMP).isoformat()),
    ]:
        assert private_client.get(path, params=params).status_code == 422
    auth(private_client, "bob")
    assert private_client.get(path).json()["scans"] == []
    with SessionLocal() as db:
        assert db.query(CandidateScan).count() == 1
