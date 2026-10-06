from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as private_setup

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_outcome import CandidateOutcome
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services import candidate_archive, candidate_outcomes
from app.services.formations import BAR


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def candidate():
    return dict(symbol="BTCUSDT", primary_pattern=dict(name="Çift dip"), evidence_score=80)


def seed(db, count=96, gap=None):
    for i in range(count):
        if i == gap:
            continue
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                interval="15m",
                open_time=STAMP + i * BAR,
                open=100,
                high=103,
                low=99,
                close=102,
                volume=1,
            )
        )
    db.commit()


def test_closed_windows_costs_pairing_and_immutable_completed(private_client):
    auth(private_client)
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        scan = CandidateScan(
            id=str(uuid4()),
            account_id=owner,
            request_id=str(uuid4()),
            created_ms=STAMP - BAR,
            rule_hash="test",
            payload=dict(evaluation_entry_ms=STAMP, candidates=[candidate()], universe=[]),
        )
        db.add(scan)
        db.commit()
        seed(db)
        before = candidate_outcomes.results(db, scan, STAMP + 4 * BAR - 1)
        assert before["summary"][0]["pending"] == 1
        short = candidate_outcomes.results(db, scan, STAMP + 16 * BAR, persist=True)
        assert [r["completed"] for r in short["summary"]] == [1, 1, 1, 0]
        assert short["paired_count"] == 0
        net = short["candidates"][0]["outcomes"][0]["net_return_pct"]
        expected = (102 * 0.9995 * 0.999 / (100 * 1.0005 * 1.001) - 1) * 100
        assert net == pytest.approx(expected)
        assert db.query(CandidateOutcome).count() == 3
        full = candidate_outcomes.results(db, scan, STAMP + 96 * BAR, persist=True)
        assert full["paired_count"] == 1
        assert db.query(CandidateOutcome).count() == 4
        db.query(BinanceSpotCandle).update(dict(close=101))
        db.commit()
        repeated = candidate_outcomes.results(db, scan, STAMP + 97 * BAR, persist=True)
        assert repeated["candidates"] == full["candidates"]


def test_gaps_and_legacy_are_not_zero_returns(private_client):
    auth(private_client)
    with SessionLocal() as db:
        seed(db, gap=1)
        scan = SimpleNamespace(
            id="missing", payload=dict(evaluation_entry_ms=STAMP, candidates=[candidate()])
        )
        result = candidate_outcomes.results(db, scan, STAMP + 96 * BAR)
        assert result["summary"][0]["unavailable"] == 1
        assert result["summary"][0]["mean_net_return_pct"] is None
        assert db.query(CandidateOutcome).count() == 0
        scan.payload.pop("evaluation_entry_ms")
        legacy = candidate_outcomes.results(db, scan, STAMP + 96 * BAR)
        assert legacy["candidates"][0]["outcomes"][0]["status"] == "legacy_unavailable"


def test_api_owner_csrf_and_cascade_delete(private_client, monkeypatch):
    headers = auth(private_client)
    monkeypatch.setattr(
        candidate_archive,
        "collect",
        lambda *args: dict(
            candidates=[candidate()], universe=[], quality_counts={}, candle_close_time="test"
        ),
    )
    monkeypatch.setattr("app.services.binance_collection.now_ms", lambda: STAMP - 2 * BAR)
    scan = private_client.post(
        "/account/candidate-scans", headers=headers, json=dict(request_id=str(uuid4()))
    ).json()
    entry = scan["scan"]["evaluation_entry_ms"]
    assert entry >= STAMP + BAR
    path = "/account/candidate-scans/" + scan["id"] + "/outcomes"
    assert private_client.post(path).status_code == 403
    with SessionLocal() as db:
        seed(db)
    # Choose the stored scan entry for this route test's mature window.
    with SessionLocal() as db:
        row = db.get(CandidateScan, scan["id"])
        row.payload = dict(row.payload, evaluation_entry_ms=STAMP)
        db.commit()
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 96 * BAR)
    headers = auth(private_client)
    assert private_client.get(path).status_code == 200
    with SessionLocal() as db:
        assert db.query(CandidateOutcome).count() == 0
    assert private_client.post(path, headers=headers).json()["paired_count"] == 1
    bob = auth(private_client, "bob")
    assert private_client.get(path).status_code == 404
    assert private_client.post(path, headers=bob).status_code == 404
    headers = auth(private_client)
    assert (
        private_client.delete("/account/candidate-scans/" + scan["id"], headers=headers).status_code
        == 200
    )
    with SessionLocal() as db:
        assert db.query(CandidateOutcome).count() == 0
