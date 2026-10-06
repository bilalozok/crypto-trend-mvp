import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.db.models.account import Account
from app.db.models.candidate_scan import CandidateScan
from app.db.models.portfolio_observation import PortfolioObservation
from app.db.models.portfolio_snapshot import PortfolioSnapshot
from app.services import portfolio_technical as technical
from tests.test_formation_history_postgres import sessions  # noqa: F401

pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="PostgreSQL CI only")


def test_concurrent_daily_observation_is_unique(sessions, monkeypatch):  # noqa: F811
    owner = str(uuid4())
    with sessions() as db:
        db.add(
            Account(
                id=owner, username="portfolio-test", password_hash="test", active=True, created_ms=0
            )
        )
        db.commit()
    monkeypatch.setattr(
        technical,
        "technical",
        lambda *args: dict(status="ready", assessment="waiting", label="Teyit bekleniyor"),
    )

    def write(stamp):
        with sessions() as db:
            return technical.save_daily(db, owner, "BTCUSDT", stamp)["created"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [1791226800000, 1791226800001]))
    assert sum(results) == 1
    with sessions() as db:
        assert db.query(PortfolioObservation).filter_by(account_id=owner).count() == 1


def test_concurrent_feed_reservation_fetches_once(sessions, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from app.services import portfolio_feeds

    bar = 14_400_000
    stamp = 1791226800000
    closed = stamp // bar * bar
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(
            status_code=200, json=lambda: [[closed - bar, "10", "11", "9", "10", "1", closed - 1]]
        )

    monkeypatch.setattr(portfolio_feeds.requests, "get", get)

    def write(_):
        with sessions() as db:
            return portfolio_feeds.refresh(db, "BTCUSDT", "4h", stamp)["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(write, [1, 2]))
    assert result.count("updated") == 1
    assert len(calls) == 1


def test_concurrent_snapshot_request_is_unique(sessions, monkeypatch):  # noqa: F811
    from app.services import portfolio_snapshots

    owner, request_id = str(uuid4()), str(uuid4())
    with sessions() as db:
        db.add(
            Account(
                id=owner, username="snapshot-test", password_hash="test", active=True, created_ms=0
            )
        )
        db.commit()
    monkeypatch.setattr(
        technical, "technical", lambda *args: dict(status="ready", version=technical.VERSION)
    )

    def write(_):
        with sessions() as db:
            return portfolio_snapshots.save(db, owner, "BTCUSDT", request_id, 1791226800000)[
                "created"
            ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [1, 2]))
    assert sum(results) == 1
    with sessions() as db:
        assert db.query(PortfolioSnapshot).filter_by(account_id=owner).count() == 1


def test_concurrent_candidate_scan_request_is_unique(sessions, monkeypatch):  # noqa: F811
    from app.services import candidate_archive

    owner, request_id = str(uuid4()), str(uuid4())
    with sessions() as db:
        db.add(
            Account(
                id=owner,
                username="candidate-scan-test",
                password_hash="test",
                active=True,
                created_ms=0,
            )
        )
        db.commit()
    monkeypatch.setattr(
        candidate_archive,
        "collect",
        lambda *args: dict(universe=[], candidates=[], quality_counts={}),
    )

    def write(_):
        with sessions() as db:
            return candidate_archive.create(db, owner, request_id, 1791226800000)["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(write, [1, 2]))
    assert ids[0] == ids[1]
    with sessions() as db:
        assert db.query(CandidateScan).filter_by(account_id=owner).count() == 1
