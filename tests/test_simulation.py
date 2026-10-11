from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as setup

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services import simulation


@pytest.fixture
def private_client(monkeypatch):
    yield from setup.__wrapped__(monkeypatch)


def order(**kwargs):
    return dict(id=str(uuid4()), symbol="BTCUSDT", source="manual", **kwargs)


def candle(close_ms, price=10):
    with SessionLocal() as db:
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                interval="15m",
                open_time=close_ms - 900000,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=10,
            )
        )
        db.commit()


def test_prospective_execution_fees_missing_bar_and_immutable_exit(private_client, monkeypatch):
    h = auth(private_client)
    candle(STAMP, 10)
    payload = order()
    r = private_client.post("/account/simulations", headers=h, json=payload)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["state"] == "pending_entry" and row["entry_price"] is None
    assert (
        private_client.post("/account/simulations", headers=h, json=payload).json()["id"]
        == row["id"]
    )
    assert (
        private_client.post(
            "/account/simulations", headers=h, json={**payload, "note": "changed"}
        ).status_code
        == 409
    )
    path = "/account/simulations/" + row["id"]
    assert private_client.post(path + "/sell", headers=h, json={}).status_code == 409
    # A later valid bar never substitutes for the exact intended entry bar.
    candle(STAMP + 1800000, 20)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 1800000)
    assert private_client.get(path).json()["state"] == "pending_entry"
    candle(STAMP + 900000, 11)
    row = private_client.get(path).json()
    assert row["state"] == "open"
    assert Decimal(row["entry_price"]) == Decimal("11") * Decimal("1.0005")
    quantity = Decimal(row["quantity"])
    expected = quantity * Decimal("20") * Decimal(".9995") * Decimal(".999") - 100
    assert abs(Decimal(row["profit"]) - expected) < Decimal("1e-8")
    sale = private_client.post(path + "/sell", headers=h, json={"note": "düşüş kontrolü"}).json()
    assert sale["state"] == "pending_exit"
    candle(STAMP + 2700000, 12)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 2700000)
    closed = private_client.get(path).json()
    assert closed["state"] == "closed"
    assert closed["sale_context"]["note"] == "düşüş kontrolü"
    assert Decimal(closed["exit_price"]) == Decimal("12") * Decimal(".9995")
    repeat = private_client.post(path + "/sell", headers=h, json={"note": "changed"}).json()
    assert repeat["exit_price"] == closed["exit_price"]
    assert repeat["sale_context"] == closed["sale_context"]
    summary = private_client.get("/account/simulations").json()["summary"]
    assert summary["closed"] == 1 and summary["win_rate_pct"] == "100.00"
    assert summary["realized_profit"] == closed["profit"]
    assert len(private_client.get("/account/simulations?kind=open").json()["positions"]) == 0
    assert private_client.delete(path, headers=h).status_code == 200
    assert private_client.get("/account/simulations").json()["summary"]["closed"] == 0


def test_owner_csrf_and_no_real_purchase_mutation(private_client):
    assert private_client.get("/account/simulations").status_code == 401
    h = auth(private_client)
    assert private_client.post("/account/simulations", json=order()).status_code == 403
    row = private_client.post("/account/simulations", headers=h, json=order()).json()
    path = "/account/simulations/" + row["id"]
    other = auth(private_client, "bob")
    assert private_client.get(path).status_code == 404
    assert private_client.post(path + "/sell", headers=other, json={}).status_code == 404
    assert private_client.delete(path, headers=other).status_code == 404
    assert private_client.get("/account/simulations").json()["positions"] == []
    auth(private_client)
    assert private_client.get("/account/purchases").json()["purchases"] == []


def test_manual_reference_and_future_price_protection(private_client):
    h = auth(private_client)
    assert (
        private_client.post("/account/simulations", headers=h, json=order(price="10")).status_code
        == 422
    )
    payload = order(mode="actual_reference", price="10", purchased_at="2026-10-05T18:00:00Z")
    response = private_client.post("/account/simulations", headers=h, json=payload)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["state"] == "open" and row["entry_price"] == "10"
    path = "/account/simulations/" + row["id"]
    assert (
        private_client.post(
            path + "/sell", headers=h, json=dict(price="9", sold_at="2026-10-05T17:00:00Z")
        ).status_code
        == 422
    )
    r = private_client.post(
        path + "/sell", headers=h, json=dict(price="9", sold_at="2026-10-05T18:30:00Z")
    ).json()
    assert r["state"] == "closed" and Decimal(r["profit"]) < 0
    assert private_client.get("/account/simulations").json()["summary"]["win_rate_pct"] == "0.00"


def test_owned_scan_snapshot_survives_scan_deletion(private_client):
    h = auth(private_client)
    scan_id = str(uuid4())
    candidate = dict(symbol="BTCUSDT", evidence_score=81, primary_pattern={"name": "Test"})
    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.username == "alice"))
        db.add(
            CandidateScan(
                id=scan_id,
                account_id=account.id,
                request_id=str(uuid4()),
                created_ms=STAMP - 900000,
                rule_hash="frozen-rules",
                payload={"candidates": [candidate]},
            )
        )
        db.commit()
    payload = {**order(), "source": "candidate_scan", "scan_id": scan_id}
    row = private_client.post("/account/simulations", headers=h, json=payload).json()
    assert "id" in row
    with SessionLocal() as db:
        db.delete(db.get(CandidateScan, scan_id))
        db.commit()
    detail = private_client.get("/account/simulations/" + row["id"]).json()
    assert detail["selection"]["candidate"] == candidate
    assert detail["selection"]["scan_rule_hash"] == "frozen-rules"


def test_filter_revalidated_and_observations_do_not_duplicate(private_client, monkeypatch):
    h = auth(private_client)
    data = {**order(), "source": "indicator_filter"}
    assert private_client.post("/account/simulations", headers=h, json=data).status_code == 409
    mock = dict(
        symbol="BTCUSDT",
        checked_at="2026-10-05T19:00:00Z",
        horizons=[
            dict(
                interval="15m",
                up=dict(status="ready", all_match=True, checks=[]),
                down=dict(status="ready", all_match=False, checks=[]),
            )
        ],
    )
    monkeypatch.setattr(simulation, "context", lambda *a: mock)
    row = private_client.post("/account/simulations", headers=h, json=data).json()
    candle(STAMP + 900000, 10)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 900000)
    path = "/account/simulations/" + row["id"]
    for _ in range(2):
        detail = private_client.get(path).json()
    assert len(detail["observations"]) == 1
    assert detail["selection"]["filter"]["criteria"]["all_match"] is True
    assert detail["selection"]["context"] == mock


def test_simulation_navigation_and_help(client):
    from collections import Counter

    from test_dashboard_navigation import Layout

    html = client.get("/analysis/binance/dashboard").text
    layout = Layout()
    layout.feed(html)
    assert all(c == 1 for c in Counter(layout.ids).values())
    assert "private-workspace-content" in layout.parents["panel-simulation"]
    assert "simulation-dialog" in layout.parents["simulation-note"]
    for key in ("positions", "add", "detail"):
        assert "panel-simulation" in layout.parents["view-simulation-pane-" + key]
    assert 'simulationBuyButton(symbol,"candidate_scan",candidateOutcomeScan)' in html
    assert 'simulationBuyButton(row.symbol,"indicator_filter"' in html
    assert '"simulation":["Simülasyon"' in html


def test_pending_and_missing_open_values_are_not_zero(private_client):
    h = auth(private_client)
    pending = private_client.post("/account/simulations", headers=h, json=order()).json()
    reference = order(mode="actual_reference", price="10", purchased_at="2026-10-05T18:00:00Z")
    private_client.post("/account/simulations", headers=h, json=reference)
    result = private_client.get("/account/simulations").json()
    assert result["summary"]["pending_entry"] == 1
    assert result["summary"]["unrealized_profit"] is None
    assert result["summary"]["win_rate_pct"] is None
    assert all(r["profit"] is None for r in result["positions"])
    assert pending["id"] in {r["id"] for r in result["positions"]}


def test_admin_user_deletion_cleans_simulations(monkeypatch):
    from test_admin_accounts import admin_client as admin_setup

    generator = admin_setup.__wrapped__(monkeypatch)
    client = next(generator)
    try:
        headers = auth(client)
        row = client.post("/account/simulations", headers=headers, json=order())
        assert row.status_code == 201, row.text
        admin = auth(client, "bilalozok")
        result = client.request(
            "DELETE",
            "/account/admin/users/alice",
            headers=admin,
            json=dict(confirm_username="alice"),
        )
        assert result.status_code == 200
        from app.db.models.simulation import Simulation

        with SessionLocal() as db:
            assert db.get(Simulation, row.json()["id"]) is None
    finally:
        generator.close()


def test_formation_scan_filter_provenance(private_client):
    h = auth(private_client)
    payload = {
        **order(),
        "source": "formation_scan",
        "scan_filters": dict(
            pattern="double_bottom",
            state="confirmed",
            direction="up",
            min_quote_volume="100000",
            fresh=True,
            volume_supported=True,
        ),
    }
    r = private_client.post("/account/simulations", headers=h, json=payload)
    assert r.status_code == 201, r.text
    detail = private_client.get("/account/simulations/" + r.json()["id"]).json()
    filters = detail["selection"]["displayed_scan_filters"]
    assert filters["pattern"] == "double_bottom" and filters["fresh"] is True
    assert filters["min_quote_volume"] == "100000"
