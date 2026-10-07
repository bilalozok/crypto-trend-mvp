from uuid import uuid4

import pytest
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as account_setup

from app.db.models.account import Account
from app.db.models.candidate_observation import CandidateObservation
from app.db.models.candidate_outcome import CandidateOutcome
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services.candidate_tracking import overview
from app.services.formations import BAR


@pytest.fixture
def private_client(monkeypatch):
    yield from account_setup.__wrapped__(monkeypatch)


def scan(db, entry=STAMP, symbols=("BTCUSDT",)):
    row = CandidateScan(
        id=str(uuid4()),
        account_id=db.query(Account).filter_by(username="alice").one().id,
        request_id=str(uuid4()),
        created_ms=STAMP - BAR,
        rule_hash="test",
        payload=dict(
            candidates=[dict(symbol=s) for s in symbols],
            universe=[],
            **({"evaluation_entry_ms": entry} if entry is not None else {}),
        ),
    )
    db.add(row)
    db.commit()
    return row


def test_tracking_phase_and_empty_reasons(private_client):
    with SessionLocal() as db:
        row = scan(db)
        assert overview(db, row, STAMP - 1)["state"] == "before_entry"
        assert overview(db, row, STAMP)["state"] == "first_close_pending"
        data = overview(db, row, STAMP + BAR)
        assert data["state"] == "window_open"
        assert "Henüz otomatik gözlem" in data["note"]
        assert data["totals"] == dict(
            stored_completed=0, time_pending=4, due_not_stored=0, legacy_unavailable=0
        )
        assert overview(db, row, STAMP + 96 * BAR + 1)["state"] == "ended"
        assert overview(db, scan(db, entry=None), STAMP)["state"] == "legacy"
        assert overview(db, scan(db, symbols=()), STAMP + BAR)["state"] == "no_candidates"


def test_latest_observation_and_saved_results_are_not_preview_or_false_loss(private_client):
    with SessionLocal() as db:
        row = scan(db, symbols=("BTCUSDT", "ETHUSDT"))
        for symbol, close, payload in [
            ("BTCUSDT", STAMP + BAR, dict(status="ready", qualified=True)),
            ("BTCUSDT", STAMP + 2 * BAR, dict(status="rule_changed", qualified=None)),
            ("ETHUSDT", STAMP + BAR, dict(status="ready", qualified=False)),
            ("ETHUSDT", STAMP + 20 * BAR, dict(status="ready", qualified=True)),
        ]:
            db.add(
                CandidateObservation(scan_id=row.id, symbol=symbol, close_ms=close, payload=payload)
            )
        db.add(
            CandidateOutcome(
                scan_id=row.id, symbol="BTCUSDT", horizon_bars=4, payload=dict(status="complete")
            )
        )
        db.commit()
        before = db.query(CandidateObservation).count()
        data = overview(db, row, STAMP + 8 * BAR)
        btc, eth = data["candidates"]
        assert btc["label"] == "Değerlendirilemedi"
        assert btc["qualified"] is None
        assert eth["label"] == "Son gözlemde aday koşulları yok"
        assert data["totals"] == dict(
            stored_completed=1, time_pending=4, due_not_stored=3, legacy_unavailable=0
        )
        assert btc["latest_expected_close_recorded"] is False
        assert db.query(CandidateObservation).count() == before
        assert db.query(CandidateOutcome).count() == 1


def test_tracking_requires_owner_and_does_not_write(private_client):
    with SessionLocal() as db:
        row = scan(db)
        row_id = row.id
    path = "/account/candidate-scans/" + row_id + "/tracking"
    assert private_client.get(path).status_code == 401
    auth(private_client, "bob")
    assert private_client.get(path).status_code == 404
    auth(private_client)
    response = private_client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    with SessionLocal() as db:
        assert db.query(CandidateObservation).count() == 0
        assert db.query(CandidateOutcome).count() == 0
