from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_portfolio_technical import buy, seed
from test_private_purchases import auth
from test_private_purchases import private_client as private_setup

from app.db.models.account import Account
from app.db.models.portfolio_snapshot import PortfolioSnapshot
from app.db.session import SessionLocal
from app.services import portfolio_auto as auto
from app.services import portfolio_snapshots, portfolio_technical


@pytest.fixture
def private_client(monkeypatch):
    yield from private_setup.__wrapped__(monkeypatch)


def stamp(hour, minute=0):
    return int(datetime(2026, 10, 5, hour, minute, tzinfo=UTC).timestamp() * 1000)


def test_turkey_schedule_and_deterministic_day():
    assert auto.schedule(stamp(5, 59))[2] is False
    day, planned, due = auto.schedule(stamp(6))
    assert due is True and planned.hour == 9
    assert planned.utcoffset().total_seconds() == 10800
    assert day == "2026-10-05"
    assert auto.schedule(stamp(22))[0] == "2026-10-06"
    assert auto.request_id("owner", "BTCUSDT", day) == auto.request_id("owner", "BTCUSDT", day)
    assert auto.request_id("other", "BTCUSDT", day) != auto.request_id("owner", "BTCUSDT", day)


def test_auto_once_retry_and_manual_records_preserved(private_client, monkeypatch):
    headers = auth(private_client)
    buy(private_client, headers)
    seed()
    monkeypatch.setattr(
        portfolio_technical,
        "technical",
        lambda *args: dict(
            status="ready",
            version=portfolio_technical.VERSION,
            horizons=[],
        ),
    )
    with SessionLocal() as db:
        owner = db.query(Account).filter_by(username="alice").one().id
        assert auto.run_symbol(db, "BTCUSDT", stamp(6)) == []
        monkeypatch.setenv("PORTFOLIO_DAILY_ENABLED", "true")
        assert auto.run_symbol(db, "BTCUSDT", stamp(5, 59)) == []
        assert auto.run_symbol(db, "BTCUSDT", stamp(6, 5)) == ["saved"]
        assert auto.run_symbol(db, "BTCUSDT", stamp(7)) == ["already_saved"]
        assert auto.status(db, owner, "BTCUSDT", stamp(7))["state"] == "saved"
        row = db.query(PortfolioSnapshot).one()
        assert row.observed_ms == stamp(6, 5)
        assert row.comparison["scheduled_for"].endswith("09:00:00+03:00")
        assert row.comparison["source"] == "automatic"
        assert portfolio_snapshots.save(db, owner, "BTCUSDT", str(uuid4()), stamp(7))["created"]
        assert auto.run_symbol(db, "BTCUSDT", stamp(7)) == ["already_saved"]
        assert db.query(PortfolioSnapshot).count() == 2
        assert auto.run_symbol(db, "ETHUSDT", stamp(7)) == []
        db.query(Account).filter_by(id=owner).update(dict(active=False))
        db.commit()
        assert auto.run_symbol(db, "BTCUSDT", stamp(6) + 86400000) == []


def test_missing_data_retries_today_not_yesterday(private_client, monkeypatch):
    headers = auth(private_client)
    buy(private_client, headers)
    monkeypatch.setenv("PORTFOLIO_DAILY_ENABLED", "true")
    monkeypatch.setattr(portfolio_technical, "technical", lambda *args: dict(status="stale_data"))
    with SessionLocal() as db:
        assert auto.run_symbol(db, "BTCUSDT", stamp(7)) == ["not_ready"]
        assert db.query(PortfolioSnapshot).count() == 0
        monkeypatch.setattr(
            portfolio_technical,
            "technical",
            lambda *args: dict(
                status="ready",
                version=portfolio_technical.VERSION,
                horizons=[],
            ),
        )
        assert auto.run_symbol(db, "BTCUSDT", stamp(8)) == ["saved"]
        assert auto.run_symbol(db, "BTCUSDT", stamp(6) + 86400000) == ["saved"]
        assert db.query(PortfolioSnapshot).count() == 2
