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


def test_summary_changes_skip_unknown_and_future(private_client):
    with SessionLocal() as db:
        row = scan(db)
        for n, status, value in [
            (1, "ready", True),
            (2, "ready", False),
            (3, "rule_changed", None),
            (4, "ready", True),
            (5, "ready", False),
            (6, "ready", True),
        ]:
            db.add(
                CandidateObservation(
                    scan_id=row.id,
                    symbol="BTCUSDT",
                    close_ms=STAMP + n * BAR,
                    payload=dict(status=status, qualified=value, close_price="10"),
                )
            )
        db.commit()
        result = overview(db, row, STAMP + 4 * BAR)["candidates"][0]
        assert result["observation_count"] == 4
        assert (
            result["first_close_time"] == overview(db, row, STAMP + BAR)["last_observation_close"]
        )
        assert (
            result["last_change_close_time"]
            == overview(db, row, STAMP + 2 * BAR)["last_observation_close"]
        )
        assert result["last_close_price"] == "10"
        later = overview(db, row, STAMP + 5 * BAR)["candidates"][0]
        assert later["last_change_close_time"] == later["last_close_time"]


def test_candidate_feed_refresh_security_and_frozen_scan(private_client, monkeypatch):
    from app.services import portfolio_feeds, portfolio_technical

    calls = []
    monkeypatch.setattr(
        portfolio_feeds,
        "refresh_symbol",
        lambda db, symbol, stamp: (
            calls.append(symbol) or {"4h": dict(status="updated"), "1d": dict(status="cached")}
        ),
    )
    monkeypatch.setattr(
        portfolio_technical, "technical", lambda db, symbol, stamp: dict(horizons=[], symbol=symbol)
    )
    with SessionLocal() as db:
        row = scan(db)
        row_id, frozen = row.id, row.payload.copy()
    path = "/account/candidate-scans/" + row_id + "/refresh"
    assert private_client.post(path, json=dict(symbol="BTCUSDT")).status_code == 401
    bob = auth(private_client, "bob")
    assert private_client.post(path, headers=bob, json=dict(symbol="BTCUSDT")).status_code == 404
    alice = auth(private_client)
    assert private_client.post(path, json=dict(symbol="BTCUSDT")).status_code == 403
    assert private_client.post(path, headers=alice, json=dict(symbol="ETHUSDT")).status_code == 404
    assert not calls
    response = private_client.post(path, headers=alice, json=dict(symbol="btcusdt"))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert calls == ["BTCUSDT"]
    with SessionLocal() as db:
        assert db.get(CandidateScan, row_id).payload == frozen
        assert db.query(CandidateOutcome).count() == 0
        assert db.query(CandidateObservation).count() == 0


def test_change_summary_regained_unknown_reset_and_exclusive_counts(private_client):
    with SessionLocal() as db:
        row = scan(db, symbols=("BTCUSDT", "ETHUSDT", "QIUSDT", "TIAUSDT", "ONGUSDT"))
        points = [
            ("BTCUSDT", 1, "ready", True),
            ("ETHUSDT", 1, "ready", True),
            ("ETHUSDT", 2, "ready", False),
            ("QIUSDT", 1, "ready", False),
            ("QIUSDT", 2, "ready", True),
            ("QIUSDT", 3, "ready", True),
            ("ONGUSDT", 1, "ready", False),
            ("ONGUSDT", 2, "rule_changed", None),
            ("ONGUSDT", 3, "ready", True),
        ]
        for symbol, n, status, value in points:
            db.add(
                CandidateObservation(
                    scan_id=row.id,
                    symbol=symbol,
                    close_ms=STAMP + n * BAR,
                    payload=dict(status=status, qualified=value),
                )
            )
        db.commit()
        result = overview(db, row, STAMP + 3 * BAR)
        assert result["change_counts"] == dict(retained=2, absent=1, regained=1, unassessed=1)
        rows = {r["symbol"]: r for r in result["candidates"]}
        assert rows["QIUSDT"]["change_label"] == "Yeniden adaylık gözlendi"
        assert rows["ONGUSDT"]["change_group"] == "retained"
        db.add(
            CandidateObservation(
                scan_id=row.id,
                symbol="QIUSDT",
                close_ms=STAMP + 4 * BAR,
                payload=dict(status="stale_data", qualified=None),
            )
        )
        db.commit()
        assert overview(db, row, STAMP + 4 * BAR)["change_counts"] == dict(
            retained=2, absent=1, regained=0, unassessed=2
        )
