from uuid import uuid4

import pytest
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as private_setup

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_observation import CandidateObservation
from app.db.models.candidate_outcome import CandidateOutcome  # noqa: F401
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services.candidate_archive import archive_hash
from app.services.candidate_observer import observe_symbol
from app.services.formations import BAR


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def setup(db, owner, rule=None):
    sid = str(uuid4())
    db.add(
        CandidateScan(
            id=sid,
            account_id=owner,
            request_id=str(uuid4()),
            created_ms=STAMP - BAR,
            rule_hash=rule or archive_hash(),
            payload=dict(evaluation_entry_ms=STAMP, candidates=[dict(symbol="BTCUSDT")]),
        )
    )
    db.commit()
    return sid


def seed(db):
    for i in range(200):
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                interval="15m",
                open_time=STAMP - (199 - i) * BAR,
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1,
            )
        )
    db.commit()


def test_closed_live_only_immutable_and_owner_delete(private_client, monkeypatch):
    headers = auth(private_client)
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        sid = setup(db, owner)
        seed(db)
        monkeypatch.delenv("CANDIDATE_OBSERVATIONS_ENABLED", raising=False)
        assert observe_symbol(db, "BTCUSDT", STAMP + BAR) == 0
        monkeypatch.setenv("CANDIDATE_OBSERVATIONS_ENABLED", "true")
        assert observe_symbol(db, "BTCUSDT", STAMP + BAR - 1) == 0
        assert observe_symbol(db, "BTCUSDT", STAMP + BAR) == 1
        original = db.query(CandidateObservation).one().payload.copy()
        assert original["qualified"] is False
        assert original["close_price"] == "100"
        db.query(BinanceSpotCandle).update(dict(close=100.5))
        db.commit()
        assert observe_symbol(db, "BTCUSDT", STAMP + BAR + 1) == 0
        assert db.query(CandidateObservation).one().payload == original
        assert observe_symbol(db, "BTCUSDT", STAMP + 3 * BAR) == 1
        # No backfilled observation for the skipped second candle.
        assert db.query(CandidateObservation).count() == 2
        latest = (
            db.query(CandidateObservation).order_by(CandidateObservation.close_ms.desc()).first()
        )
        assert latest.payload["qualified"] is None
        assert observe_symbol(db, "BTCUSDT", STAMP + 97 * BAR) == 0
    path = "/account/candidate-scans/" + sid + "/observations"
    assert private_client.get(path).json()["total"] == 2
    assert (
        private_client.delete("/account/candidate-scans/" + sid, headers=headers).status_code == 200
    )
    with SessionLocal() as db:
        assert db.query(CandidateObservation).count() == 0
    auth(private_client, "bob")
    assert private_client.get(path).status_code == 404


def test_rule_mismatch_and_inactive_are_not_loss(private_client, monkeypatch):
    auth(private_client)
    monkeypatch.setenv("CANDIDATE_OBSERVATIONS_ENABLED", "true")
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        setup(db, owner, "different")
        seed(db)
        assert observe_symbol(db, "BTCUSDT", STAMP + BAR) == 1
        row = db.query(CandidateObservation).one()
        assert row.payload["status"] == "rule_changed"
        assert row.payload["qualified"] is None
        db.query(Account).filter_by(id=owner).update(dict(active=False))
        db.commit()
        assert observe_symbol(db, "BTCUSDT", STAMP + 2 * BAR) == 0
