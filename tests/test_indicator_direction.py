from copy import deepcopy

import pytest
from test_private_purchases import STAMP, auth, record
from test_private_purchases import private_client as account_setup

from app.services.indicator_direction import evaluate


def horizon(up=True):
    sign = 1 if up else -1
    latest = dict(
        at="2026-10-10T12:00:00+00:00",
        close=100,
        rsi=60 if up else 40,
        histogram=sign,
        sma50=100 - sign,
        ema50=100 - sign,
        bb_middle=100 - sign,
        bb_lower=90,
        bb_upper=110,
    )
    return dict(
        status="ready",
        latest=latest,
        series=[dict(ema50=100 - 2 * sign), latest],
        ichimoku=dict(
            status="ready",
            position="above" if up else "below",
            latest=dict(tenkan=100 + sign, kijun=100),
        ),
        money_flow=dict(status="ready", latest=dict(cmf=sign * 0.1, mfi=60 if up else 40)),
        fibonacci=dict(
            status="ready",
            direction="up" if up else "down",
            levels=[dict(ratio=0.618, price=100 - sign)],
        ),
        confluence=dict(
            patterns=[
                dict(
                    direction="up" if up else "down",
                    stage="confirmed",
                    groups=[dict(name="Teyit hacmi", state="supports")],
                )
            ]
        ),
    )


def test_directional_checklist_and_boundaries():
    for up in [True, False]:
        h = horizon(up)
        result = evaluate(h, "up" if up else "down")
        assert result["all_match"]
        assert len(result["checks"]) == 8
        h["latest"]["rsi"] = 70 if up else 30
        assert not evaluate(h, "up" if up else "down")["all_match"]


def test_missing_conflicting_and_no_forming_confirmation():
    h = horizon()
    h["money_flow"] = {"status": "insufficient_data"}
    assert not evaluate(h, "up")["all_match"]
    h = horizon()
    h["confluence"]["patterns"][0]["stage"] = "forming"
    assert not evaluate(h, "up")["all_match"]
    h = horizon()
    h["confluence"]["patterns"].append(dict(direction="down", stage="confirmed", groups=[]))
    assert not evaluate(h, "up")["all_match"]
    assert evaluate(dict(status="stale_data"), "up")["checks"] == []
    h = horizon()
    before = deepcopy(h)
    evaluate(h, "up")
    assert h == before


@pytest.fixture
def private_client(monkeypatch):
    yield from account_setup.__wrapped__(monkeypatch)


def test_routes_require_login_and_owner_scope(private_client):
    for path in ["/account/indicator-screen", "/account/indicator-direction?symbol=BTCUSDT"]:
        assert private_client.get(path).status_code == 401
    headers = auth(private_client, "alice")
    assert (
        private_client.post("/account/purchases", json=record(), headers=headers).status_code == 201
    )
    result = private_client.get("/account/indicator-screen?scope=held&interval=4h")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["total_symbols"] == 1
    assert result.json()["matches"] == []
    assert result.json()["quality_counts"] == {"insufficient_data": 1}
    auth(private_client, "bob")
    assert private_client.get("/account/indicator-screen?scope=held").json()["total_symbols"] == 0
    assert private_client.get("/account/indicator-screen?scope=market").json()["total_symbols"] == 1
    assert private_client.get(f"/account/indicator-screen?as_of={STAMP + 1}").status_code == 422
    assert private_client.get("/account/indicator-screen?interval=5m").status_code == 422
    assert private_client.get("/account/indicator-direction?symbol=BTCUSDT").status_code == 200


def test_market_pagination_is_complete_without_duplicates(private_client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotSymbol
    from app.db.session import SessionLocal
    from app.services import indicator_direction

    monkeypatch.setattr(indicator_direction, "horizon", lambda *args: horizon())
    with SessionLocal() as db:
        for symbol in ("ETHUSDT", "SFPUSDT"):
            db.add(
                BinanceSpotSymbol(
                    symbol=symbol,
                    base_asset=symbol[:-4],
                    active=True,
                    quote_volume_24h=1,
                    catalog_updated_ms=0,
                )
            )
        db.commit()
        symbols = []
        offset = 0
        while offset is not None:
            result = indicator_direction.screen(
                db, "unrelated-account", "market", "15m", STAMP, offset, limit=1
            )
            assert result["total_symbols"] == 3
            assert result["scanned_symbols"] == 1
            symbols.extend(r["symbol"] for r in result["matches"])
            offset = result["next_offset"]
        assert symbols == ["BTCUSDT", "ETHUSDT", "SFPUSDT"]
