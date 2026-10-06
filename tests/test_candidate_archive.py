from copy import deepcopy
from uuid import uuid4

import pytest
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as private_setup

from app.db.session import SessionLocal
from app.services import candidate_archive as archive


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def payload():
    return dict(
        candle_close_time="2026-10-05T19:00:00+00:00",
        quality_counts={"ready": 1},
        universe=[
            dict(
                symbol="BTCUSDT",
                status="ready",
                qualified=True,
                reason=None,
                note="Aday",
                assessment="bullish_setup",
            )
        ],
        candidates=[dict(symbol="BTCUSDT", evidence_score=80)],
    )


def test_private_scan_csrf_owner_idempotence_and_frozen(private_client, monkeypatch):
    path = "/account/candidate-scans"
    body = dict(request_id=str(uuid4()))
    assert private_client.post(path, json=body).status_code == 401
    headers = auth(private_client)
    assert private_client.post(path, json=body).status_code == 403
    original = payload()
    monkeypatch.setattr(archive, "collect", lambda *args: deepcopy(original))
    first = private_client.post(path, json=body, headers=headers)
    assert first.status_code == 201, first.json()
    stored = first.json()
    original["candidates"][0]["evidence_score"] = 20
    assert private_client.post(path, json=body, headers=headers).json() == stored
    assert private_client.get(path + "/" + stored["id"]).json() == stored
    second = private_client.post(path, json=dict(request_id=str(uuid4())), headers=headers).json()
    assert second["id"] != stored["id"]
    assert second["scan"]["candidates"][0]["evidence_score"] == 20
    assert len(private_client.get(path).json()["scans"]) == 2
    assert private_client.get(path).headers["cache-control"] == "no-store"
    auth(private_client, "bob")
    assert private_client.get(path).json()["scans"] == []
    assert private_client.get(path + "/" + stored["id"]).status_code == 404
    assert private_client.get(path + "/history?symbol=BTCUSDT").json()["events"] == []


def test_history_separates_exclusion_missing_and_absent(private_client, monkeypatch):
    headers = auth(private_client)
    original = payload()
    monkeypatch.setattr(archive, "collect", lambda *args: deepcopy(original))
    path = "/account/candidate-scans"
    for status in ("ready", "stale_data", "absent"):
        original["candidates"] = []
        if status == "absent":
            original["universe"] = []
        else:
            original["universe"][0].update(status=status, qualified=False, note=status)
        assert (
            private_client.post(
                path, json=dict(request_id=str(uuid4())), headers=headers
            ).status_code
            == 201
        )
    events = private_client.get(path + "/history?symbol=BTCUSDT").json()["events"]
    assert len(events) == 3
    assert sum(e["state"] is None for e in events) == 1
    assert {e["state"]["status"] for e in events if e["state"]} == {"ready", "stale_data"}


def test_real_collect_records_missing_data_without_false_candidate(private_client):
    auth(private_client)
    with SessionLocal() as db:
        data = archive.collect(db, STAMP)
    assert len(data["universe"]) == 1
    assert data["universe"][0]["qualified"] is False
    assert data["universe"][0]["reason"] == "data_unavailable"
    assert data["candidates"] == []


def test_collect_preserves_ranking_and_missing_higher_intervals(private_client, monkeypatch):
    auth(private_client)
    from test_portfolio_technical import seed

    seed()
    pattern = dict(
        pattern="double_bottom",
        name="Çift dip",
        status="confirmed",
        direction="up",
        current_confirmation=True,
        report_volume_supported=True,
        confirmation_age_bars=0,
        breakout_holding=True,
        volume_ratio=3,
        distance_from_breakout_pct=0,
    )
    monkeypatch.setattr(
        archive, "build_report", lambda *args: dict(patterns=[pattern], assessment="bullish_setup")
    )
    monkeypatch.setattr(
        archive.portfolio_technical,
        "technical",
        lambda *args: dict(
            horizons=[
                dict(interval="15m", status="ready"),
                dict(interval="4h", status="insufficient_data"),
                dict(interval="1d", status="insufficient_data"),
            ],
            alignment="Eksik vadeler",
            new_purchase_review="Veriyi kontrol et",
            holding_review="İzle",
        ),
    )
    with SessionLocal() as db:
        data = archive.collect(db, STAMP)
    candidate = data["candidates"][0]
    assert candidate["evidence_score"] == 100
    assert candidate["horizons"][1]["status"] == "insufficient_data"
    assert data["ranking_scope"] == "saved_full_universe"
