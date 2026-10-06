from uuid import uuid4

import pytest
from test_portfolio_technical import buy, seed
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as private_setup

from app.db.models.portfolio_snapshot import PortfolioSnapshot
from app.db.session import SessionLocal
from app.services import portfolio_snapshots as snapshots
from app.services import portfolio_technical as technical


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def test_snapshot_auth_owner_csrf_and_immutable_daily(private_client, monkeypatch):
    path = "/account/portfolio/snapshots"
    body = dict(symbol="BTCUSDT", request_id=str(uuid4()))
    assert private_client.post(path, json=body).status_code == 401
    headers = auth(private_client)
    buy(private_client, headers)
    seed()
    assert private_client.post(path, json=body).status_code == 403
    daily = private_client.post(
        "/account/portfolio/observations", json=dict(symbol="BTCUSDT"), headers=headers
    ).json()
    first = private_client.post(path, json=body, headers=headers).json()
    assert first.get("created") is True, first
    assert first["snapshot"]["comparison"]["previous_at"] == daily["observation"]["observed_at"]
    assert private_client.post(path, json=body, headers=headers).json() == dict(
        created=False, snapshot=first["snapshot"]
    )
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 1)
    newer = private_client.post(
        path, json=dict(symbol="BTCUSDT", request_id=str(uuid4())), headers=headers
    ).json()
    assert newer["created"] is True
    assert newer["snapshot"]["id"] != first["snapshot"]["id"]
    history = private_client.get(path + "?symbol=BTCUSDT").json()["snapshots"]
    assert len(history) == 2
    assert history[0] == newer["snapshot"]
    assert (
        private_client.get("/account/portfolio/history?symbol=BTCUSDT").json()["observations"][0]
        == daily["observation"]
    )
    auth(private_client, "bob")
    assert private_client.get(path + "?symbol=BTCUSDT").status_code == 404
    bob = {"X-CSRF-Token": private_client.get("/account/session").json()["csrf_token"]}
    assert private_client.post(path, json=body, headers=bob).status_code == 404


def test_unready_not_saved(private_client):
    headers = auth(private_client)
    buy(private_client, headers)
    result = private_client.post(
        "/account/portfolio/snapshots",
        headers=headers,
        json=dict(symbol="BTCUSDT", request_id=str(uuid4())),
    ).json()
    assert result["reason"] == "not_ready"
    with SessionLocal() as db:
        assert db.query(PortfolioSnapshot).count() == 0


def view(status="confirmed", current=True, supported=True):
    return dict(
        version=technical.VERSION,
        horizons=[
            dict(
                name="Kısa",
                interval="15m",
                status="ready",
                assessment="waiting",
                label="Bekle",
                patterns=[
                    dict(
                        pattern="double_bottom",
                        name="Çift dip",
                        status=status,
                        current_confirmation=current,
                        direction="up",
                        report_volume_supported=supported,
                    )
                ],
            )
        ],
    )


def test_comparison_loss_invalidation_new_and_volume():
    notes = " ".join(snapshots.compare(view(), view("invalidated", False)))
    assert "teyit kayboldu" in notes
    assert "geçersizleşti" in notes
    assert "yeni güncel teyit" in " ".join(snapshots.compare(view("forming", False), view()))
    assert "hacim desteği değişti" in " ".join(snapshots.compare(view(), view(supported=False)))
    old = view()
    old["horizons"][0]["status"] = "stale_data"
    assert "karşılaştırılmadı" in " ".join(snapshots.compare(old, view()))
    old["version"] = "old"
    assert "sürümü farklı" in " ".join(snapshots.compare(old, view()))
