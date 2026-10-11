from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as setup

from app.db.models.account import Account
from app.db.models.candidate_scan import CandidateScan
from app.db.models.simulation import Simulation, SimulationCandle
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
            SimulationCandle(
                symbol="BTCUSDT",
                open_time=close_ms - 300000,
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
    candle(STAMP + 600000, 20)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 600000)
    assert private_client.get(path).json()["state"] == "pending_entry"
    candle(STAMP + 300000, 11)
    row = private_client.get(path).json()
    assert row["state"] == "open"
    assert Decimal(row["entry_price"]) == Decimal("11") * Decimal("1.0005")
    quantity = Decimal(row["quantity"])
    expected = quantity * Decimal("20") * Decimal(".9995") * Decimal(".999") - 100
    assert abs(Decimal(row["profit"]) - expected) < Decimal("1e-8")
    sale = private_client.post(path + "/sell", headers=h, json={"note": "düşüş kontrolü"}).json()
    assert sale["state"] == "pending_exit"
    candle(STAMP + 900000, 12)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 900000)
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
    candle(STAMP + 300000, 10)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 300000)
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


def test_legacy_pending_entry_keeps_15m_but_new_sale_uses_5m(private_client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle

    h = auth(private_client)
    row = private_client.post("/account/simulations", headers=h, json=order()).json()
    with SessionLocal() as db:
        legacy = db.get(Simulation, row["id"])
        legacy.entry_interval = "15m"
        legacy.entry_ms = STAMP + 900000
        db.add(
            BinanceSpotCandle(
                symbol="BTCUSDT",
                interval="15m",
                open_time=STAMP,
                open=10,
                high=11,
                low=9,
                close=10,
                volume=10,
            )
        )
        db.commit()
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 900000)
    path = "/account/simulations/" + row["id"]
    legacy = private_client.get(path).json()
    assert legacy["state"] == "open" and legacy["entry_interval"] == "15m"
    assert legacy["current_price"] is None  # Never substitute 15m for missing 5m marks.
    sale = private_client.post(path + "/sell", headers=h, json={}).json()
    assert sale["exit_interval"] == "5m"
    assert sale["exit_at"] == simulation.timestamp(STAMP + 1200000)


def test_latest_formation_uses_current_direction_and_handles_ties(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.services import coin_report, formations

    at = datetime(2026, 10, 11, 15, tzinfo=UTC)
    patterns = [
        dict(
            name="Eski yükseliş",
            direction="up",
            status="confirmed",
            current_confirmation=True,
            confirmed_at=at - timedelta(minutes=15),
        ),
        dict(
            name="Yeni düşüş",
            direction="down",
            status="forming",
            current_confirmation=False,
            structure_available_at=at,
        ),
        dict(name="Geçersiz", direction="up", status="invalidated", current_confirmation=False),
    ]
    monkeypatch.setattr(formations, "analyze_rows", lambda *a: dict(status="ready"))
    monkeypatch.setattr(coin_report, "build_report", lambda *a: dict(patterns=patterns))
    with SessionLocal() as db:
        result = simulation.latest_formation(db, "BTCUSDT", STAMP)
        assert result["direction"] == "down" and result["stage"] == "forming"
        patterns.append(
            dict(
                name="Aynı an yükseliş",
                direction="up",
                status="forming",
                current_confirmation=False,
                structure_available_at=at,
            )
        )
        assert simulation.latest_formation(db, "BTCUSDT", STAMP)["status"] == "conflicting"
        monkeypatch.setattr(formations, "analyze_rows", lambda *a: dict(status="stale_data"))
        assert simulation.latest_formation(db, "BTCUSDT", STAMP)["direction"] is None


def test_listing_refreshes_live_formations_without_changing_selection(private_client, monkeypatch):
    h = auth(private_client)
    row = private_client.post("/account/simulations", headers=h, json=order()).json()
    candle(STAMP + 300000)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 300000)
    monkeypatch.setattr(
        simulation, "latest_formation", lambda *a: dict(status="ready", direction="down")
    )
    data = private_client.get("/account/simulations").json()["positions"][0]
    assert data["current_formation"]["direction"] == "down"
    assert Decimal(data["profit"]) == Decimal(data["return_pct"])
    detail = private_client.get("/account/simulations/" + row["id"]).json()
    assert "current_formation" not in detail["selection"]


def test_price_chart_keeps_sale_marker_and_later_prices(private_client, monkeypatch):
    h = auth(private_client)
    row = private_client.post("/account/simulations", headers=h, json=order()).json()
    path = "/account/simulations/" + row["id"]
    candle(STAMP + 300000, 10)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 300000)
    assert private_client.get(path).json()["state"] == "open"
    private_client.post(path + "/sell", headers=h, json={})
    candle(STAMP + 600000, 11)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 600000)
    sold = private_client.get(path).json()
    candle(STAMP + 900000, 15)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 900000)
    after = private_client.get(path).json()
    assert after["profit"] == sold["profit"]
    chart = after["price_history"]
    assert chart["entry"]["price"] == after["entry_price"]
    assert chart["sale"]["price"] == sold["exit_price"]
    assert chart["points"][-1]["price"] == "15.0"
    assert chart["points"][-1]["at"] > chart["sale"]["at"]
    assert private_client.delete(path, headers=h).status_code == 200
    assert private_client.get(path).status_code == 404


def test_price_chart_reports_gap_and_bounds_long_history(private_client):
    h = auth(private_client)
    response = private_client.post("/account/simulations", headers=h, json=order()).json()
    candle(STAMP + 300000, 10)
    candle(STAMP + 900000, 12)
    with SessionLocal() as db:
        row = db.get(Simulation, response["id"])
        chart = simulation.price_history(db, row, STAMP + 900000)
        assert chart["points"][-1]["gap_before"] is True
        chart = simulation.price_history(db, row, STAMP + 300000 * 100000)
        assert chart["sample_minutes"] >= 500
        assert len(chart["points"]) <= 1001


def test_basket_totals_deduplicate_coins_and_exclude_pending_budget(private_client):
    h = auth(private_client)
    first = private_client.post(
        "/account/simulations",
        headers=h,
        json=order(mode="actual_reference", price="10", purchased_at="2026-10-05T18:00:00Z"),
    ).json()
    private_client.post(
        "/account/simulations",
        headers=h,
        json=order(mode="actual_reference", price="12", purchased_at="2026-10-05T18:00:00Z"),
    )
    private_client.post("/account/simulations", headers=h, json=order())
    candle(STAMP, 11)
    data = private_client.get("/account/simulations").json()
    basket = data["summary"]["basket"]
    assert basket["active_coins"] == 1
    assert basket["profitable_coins"] == 1 and basket["losing_coins"] == 0
    assert basket["total_purchase_budget"] == "200.00000000"
    assert basket["pending_budget"] == "100.00000000"
    assert basket["largest_coin_weight_pct"] == "100.00"
    expected = sum(Decimal(p["profit"]) for p in data["positions"] if p["profit"] is not None)
    assert Decimal(basket["total_profit"]) == expected
    assert abs(Decimal(basket["total_return_pct"]) - expected / 2) < Decimal("1e-8")
    assert (
        private_client.get("/account/simulations?offset=20&kind=closed").json()["summary"]
        == data["summary"]
    )
    private_client.post(
        "/account/simulations/" + first["id"] + "/sell",
        headers=h,
        json=dict(price="10", sold_at="2026-10-05T18:30:00Z"),
    )
    after = private_client.get("/account/simulations").json()["summary"]
    assert after["closed"] == 1
    assert after["basket"]["losing_coins"] == 1
    assert after["basket"]["active_purchase_budget"] == "100.00000000"
    assert after["basket"]["total_purchase_budget"] == "200.00000000"


def test_basket_missing_prices_never_show_partial_total(private_client):
    h = auth(private_client)
    private_client.post(
        "/account/simulations",
        headers=h,
        json=order(mode="actual_reference", price="10", purchased_at="2026-10-05T18:00:00Z"),
    )
    basket = private_client.get("/account/simulations").json()["summary"]["basket"]
    assert basket["active_coins"] == 1 and basket["missing_price_coins"] == 1
    assert basket["total_profit"] is None and basket["total_return_pct"] is None
    assert basket["active_net_value"] is None
    assert basket["profitable_coins"] == basket["losing_coins"] == basket["flat_coins"] == 0


def test_source_summary_distinguishes_open_profit_from_no_closed_trades(private_client):
    h = auth(private_client)
    private_client.post(
        "/account/simulations",
        headers=h,
        json=order(mode="actual_reference", price="10", purchased_at="2026-10-05T18:00:00Z"),
    )
    candle(STAMP, 11)
    group = private_client.get("/account/simulations").json()["source_groups"][0]
    assert group["open"] == 1 and group["closed"] == 0
    assert group["profit"] == "0.00000000" and group["win_rate_pct"] is None
    assert Decimal(group["unrealized_profit"]) > 0
    assert group["total_profit"] == group["unrealized_profit"]
    assert group["total_return_pct"] == group["total_profit"]


def test_basket_chart_is_private_and_includes_closed_price_growth(private_client, monkeypatch):
    assert private_client.get("/account/simulation-basket-chart").status_code == 401
    h = auth(private_client)
    row = private_client.post(
        "/account/simulations",
        headers=h,
        json=order(mode="actual_reference", price="10", purchased_at="2026-10-05T18:00:00Z"),
    ).json()
    private_client.post(
        "/account/simulations/" + row["id"] + "/sell",
        headers=h,
        json=dict(price="11", sold_at="2026-10-05T18:30:00Z"),
    )
    candle(STAMP, 12)
    result = private_client.get("/account/simulation-basket-chart").json()
    assert len(result["series"]) == 1
    series = result["series"][0]
    assert series["closed"] == 1 and series["symbol"] == "BTCUSDT"
    assert Decimal(series["points"][-1]["change_pct"]) == Decimal("20")
    assert len(series["points"]) <= 402
    before = private_client.get("/account/simulations/" + row["id"]).json()["profit"]
    candle(STAMP + 300000, 15)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 300000)
    after = private_client.get("/account/simulation-basket-chart").json()
    assert Decimal(after["series"][0]["points"][-1]["change_pct"]) == Decimal("50")
    assert private_client.get("/account/simulations/" + row["id"]).json()["profit"] == before
    auth(private_client, "bob")
    assert private_client.get("/account/simulation-basket-chart").json()["series"] == []
    h = auth(private_client)
    private_client.delete("/account/simulations/" + row["id"], headers=h)
    assert private_client.get("/account/simulation-basket-chart").json()["series"] == []


def test_basket_chart_weights_multiple_purchases_and_omits_pending(private_client):
    h = auth(private_client)
    for price in ("10", "20"):
        private_client.post(
            "/account/simulations",
            headers=h,
            json=order(mode="actual_reference", price=price, purchased_at="2026-10-05T18:00:00Z"),
        )
    private_client.post("/account/simulations", headers=h, json=order())
    candle(STAMP, 15)
    result = private_client.get("/account/simulation-basket-chart").json()
    assert len(result["series"]) == 1 and result["pending"] == 1
    point = result["series"][0]["points"][-1]
    assert point["entries"] == 2
    assert abs(Decimal(point["reference_price"]) - Decimal(40) / 3) < Decimal("1e-8")
    assert Decimal(point["change_pct"]) == Decimal("12.5")
