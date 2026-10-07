from uuid import uuid4

import pytest
from test_private_purchases import STAMP, auth
from test_private_purchases import private_client as private_setup

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services.formations import BAR, timestamp


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def test_observed_loss_unknown_data_rule_change_and_price(private_client):
    assert private_client.get("/account/candidate-scans/history?symbol=BTCUSDT").status_code == 401
    auth(private_client)
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        for i, (quality, qualified, rule) in enumerate(
            [
                ("ready", True, "a"),
                ("stale_data", False, "a"),
                ("ready", False, "a"),
                ("ready", False, "b"),
                ("ready", True, "b"),
            ]
        ):
            db.add(
                CandidateScan(
                    id=str(uuid4()),
                    account_id=owner,
                    request_id=str(uuid4()),
                    created_ms=STAMP + i * BAR,
                    rule_hash=rule,
                    payload=dict(
                        candle_close_time=timestamp(STAMP + i * BAR).isoformat(),
                        candidates=[],
                        universe=[
                            dict(symbol="BTCUSDT", status=quality, qualified=qualified, note="Test")
                        ],
                    ),
                )
            )
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
    d = private_client.get("/account/candidate-scans/history?symbol=BTCUSDT").json()
    rows = list(reversed(d["lifecycle"]))
    assert rows[1]["change"] == "Veri hazır değil; kayıp sayılmadı"
    assert rows[2]["change"] == "Aday koşullarının kaybı gözlendi"
    assert rows[2]["last_qualified_at"] == timestamp(STAMP).isoformat()
    assert rows[2]["close_price"] == "102"
    assert rows[0]["close_price"] is None
    assert rows[3]["change"] == "İlk uygun gözlem: aday değil"
    assert rows[3]["last_qualified_at"] is None
    assert rows[4]["change"] == "Adaylığa yeniden giriş gözlendi"
    private_client.post(
        "/account/logout",
        headers={"x-csrf-token": private_client.get("/account/session").json()["csrf_token"]},
    )
    auth(private_client, username="bob")
    assert (
        private_client.get("/account/candidate-scans/history?symbol=BTCUSDT").json()["lifecycle"]
        == []
    )
