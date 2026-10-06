from uuid import uuid4

import pytest
from test_candidate_outcomes import candidate, seed
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as private_setup

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_outcome import CandidateOutcome
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services.candidate_auto import settle_due
from app.services.formations import BAR


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def add_scan(db, owner, entry=STAMP):
    db.add(
        CandidateScan(
            id=str(uuid4()),
            account_id=owner,
            request_id=str(uuid4()),
            created_ms=STAMP,
            rule_hash="test",
            payload=dict(candidates=[candidate()], evaluation_entry_ms=entry),
        )
    )
    db.commit()


def test_disabled_pending_and_immutable(private_client, monkeypatch):
    auth(private_client)
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        add_scan(db, owner)
        seed(db)
        monkeypatch.delenv("CANDIDATE_OUTCOMES_ENABLED", raising=False)
        assert settle_due(db, STAMP + 96 * BAR) == 0
        monkeypatch.setenv("CANDIDATE_OUTCOMES_ENABLED", "true")
        assert settle_due(db, STAMP + 4 * BAR - 1) == 0
        assert settle_due(db, STAMP + 4 * BAR) == 1
        saved = db.query(CandidateOutcome).one().payload.copy()
        assert saved["net_return_pct"] < 2
        assert settle_due(db, STAMP + 96 * BAR) == 1
        assert db.query(CandidateOutcome).count() == 4
        assert settle_due(db, STAMP + 97 * BAR) == 0
        assert db.query(CandidateOutcome).filter_by(horizon_bars=4).one().payload == saved


def test_gap_retry_legacy_inactive_and_batch_limit(private_client, monkeypatch):
    auth(private_client)
    monkeypatch.setenv("CANDIDATE_OUTCOMES_ENABLED", "true")
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        for _ in range(12):
            add_scan(db, owner)
        add_scan(db, owner, None)
        seed(db, gap=1)
        assert settle_due(db, STAMP + 96 * BAR) == 10
        assert db.query(CandidateOutcome).count() == 0
        db.query(Account).filter_by(id=owner).update(dict(active=False))
        db.commit()
        assert settle_due(db, STAMP + 97 * BAR) == 0
        db.query(Account).filter_by(id=owner).update(dict(active=True))
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                interval="15m",
                open_time=STAMP + BAR,
                open=100,
                high=103,
                low=99,
                close=102,
                volume=1,
            )
        )
        db.commit()
        assert settle_due(db, STAMP + 98 * BAR) == 10
        assert db.query(CandidateOutcome).count() == 40
        assert settle_due(db, STAMP + 99 * BAR) == 2
        assert db.query(CandidateOutcome).count() == 48
