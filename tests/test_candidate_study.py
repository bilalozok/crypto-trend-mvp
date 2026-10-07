from uuid import uuid4

import pytest
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as private_setup

from app.db.models.account import Account
from app.db.models.candidate_outcome import CandidateOutcome
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services.candidate_study import report
from app.services.formations import BAR


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def add(db, owner, entry, rule="rule", value=None, pattern="Dip", partial=False):
    sid = str(uuid4())
    db.add(
        CandidateScan(
            id=sid,
            account_id=owner,
            request_id=str(uuid4()),
            created_ms=STAMP,
            rule_hash=rule,
            payload=dict(
                evaluation_entry_ms=entry,
                candidates=[dict(symbol="BTCUSDT", primary_pattern=dict(name=pattern))],
            ),
        )
    )
    db.flush()
    if value is not None:
        for h in (4,) if partial else (4, 8, 16, 96):
            db.add(
                CandidateOutcome(
                    scan_id=sid,
                    symbol="BTCUSDT",
                    horizon_bars=h,
                    payload=dict(status="complete", net_return_pct=value),
                )
            )
    db.commit()


def test_overlap_selection_is_independent_of_completion_and_pattern(private_client):
    auth(private_client)
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        add(db, owner, STAMP, partial=True, value=-5)
        add(db, owner, STAMP + BAR, value=90, pattern="Başka")
        add(db, owner, STAMP + 96 * BAR, value=-1)
        add(db, owner, STAMP, rule="other", value=3)
        add(db, owner, None)
        d = report(db, owner, STAMP + 100 * BAR, 7)
        assert d["overlap_excluded"] == 1
        assert d["legacy_excluded"] == 1
        g = next(g for g in d["groups"] if g["rule_hash"] == "rule")
        assert (g["retained"], g["paired"], g["incomplete"]) == (2, 1, 1)
        assert all(h["mean_net_return_pct"] == -1 for h in g["horizons"])
        assert len(d["groups"]) == 2
        assert report(db, "another-owner", STAMP + 100 * BAR, 7)["groups"] == []
        assert db.query(CandidateOutcome).count() == 13


def test_api_auth_owner_and_empty_summary(private_client):
    assert private_client.get("/account/candidate-scans/study").status_code == 401
    auth(private_client)
    response = private_client.get("/account/candidate-scans/study?days=7")
    assert response.status_code == 200
    assert response.json()["groups"] == []
    assert private_client.get("/account/candidate-scans/study?days=31").status_code == 422
