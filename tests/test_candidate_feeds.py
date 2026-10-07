from uuid import uuid4

import pytest
from test_private_purchases import STAMP
from test_private_purchases import private_client as account_setup

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.candidate_scan import CandidateScan
from app.db.session import SessionLocal
from app.services.candidate_feeds import tracked_symbols
from app.services.formations import BAR


@pytest.fixture
def private_client(monkeypatch):
    yield from account_setup.__wrapped__(monkeypatch)


def add(db, owner, entry, symbols):
    row = CandidateScan(
        id=str(uuid4()),
        account_id=owner,
        request_id=str(uuid4()),
        created_ms=STAMP,
        rule_hash="test",
        payload=dict(candidates=[dict(symbol=s) for s in symbols], evaluation_entry_ms=entry),
    )
    db.add(row)
    return row


def test_active_windows_deduplication_and_no_writes(private_client, monkeypatch):
    with SessionLocal() as db:
        alice = db.query(Account).filter_by(username="alice").one()
        bob = db.query(Account).filter_by(username="bob").one()
        bob.active = False
        row = add(db, alice.id, STAMP, ["BTCUSDT", "ETHUSDT"])
        add(db, alice.id, STAMP, ["BTCUSDT"])
        add(db, alice.id, None, ["LEGACYUSDT"])
        add(db, alice.id, STAMP + 200 * BAR, ["FUTUREUSDT"])
        add(db, alice.id, STAMP - 97 * BAR, ["OLDUSDT"])
        add(db, bob.id, STAMP, ["BOBUSDT"])
        for symbol in ("ETHUSDT", "LEGACYUSDT", "FUTUREUSDT", "OLDUSDT", "BOBUSDT"):
            db.add(
                BinanceSpotSymbol(
                    symbol=symbol,
                    base_asset=symbol.removesuffix("USDT"),
                    active=symbol != "ETHUSDT",
                    quote_volume_24h=1000,
                    catalog_updated_ms=STAMP,
                )
            )
        db.commit()
        original = row.payload.copy()
        assert tracked_symbols(db, STAMP) == set()
        monkeypatch.setenv("CANDIDATE_TIMEFRAMES_ENABLED", "true")
        assert tracked_symbols(db, STAMP) == {"BTCUSDT"}
        assert tracked_symbols(db, STAMP + 96 * BAR) == {"BTCUSDT"}
        assert tracked_symbols(db, STAMP + 97 * BAR) == set()
        assert db.get(CandidateScan, row.id).payload == original
        assert db.query(CandidateScan).count() == 6


def test_worker_shares_portfolio_candidate_refresh(private_client, monkeypatch):
    from types import SimpleNamespace

    from app.workers import market_worker

    monkeypatch.setenv("PORTFOLIO_TIMEFRAMES_ENABLED", "true")
    monkeypatch.setenv("CANDIDATE_TIMEFRAMES_ENABLED", "true")
    monkeypatch.setattr(
        market_worker.catalog,
        "snapshot",
        lambda: (
            STAMP,
            [SimpleNamespace(symbol="BTCUSDT", base_asset="BTC", quote_volume_24h=1000)],
        ),
    )
    monkeypatch.setattr("app.services.binance_collection.now_ms", lambda: STAMP)
    monkeypatch.setattr("app.services.binance_collection.refresh_symbol", lambda *a: 1)
    monkeypatch.setattr("app.services.formation_history.record_symbol", lambda *a: 0)
    monkeypatch.setattr(
        "app.services.forward_tracking.track_symbol", lambda *a: dict(recorded=0, settled=0)
    )
    monkeypatch.setattr("app.services.portfolio_feeds.owned_symbols", lambda db: ["BTCUSDT"])
    monkeypatch.setattr(
        "app.services.candidate_feeds.tracked_symbols", lambda db, stamp: {"BTCUSDT"}
    )
    calls = []
    monkeypatch.setattr(
        "app.services.portfolio_feeds.refresh_symbol",
        lambda db, symbol, stamp: calls.append(symbol),
    )
    monkeypatch.setattr("app.services.portfolio_auto.run_symbol", lambda *a: 0)
    monkeypatch.setattr("app.services.candidate_auto.settle_due", lambda *a: 0)
    monkeypatch.setattr("app.services.candidate_observer.observe_symbol", lambda *a: 0)
    assert market_worker.collect_market(budget=10, workers=1) == 0
    assert calls == ["BTCUSDT"]
