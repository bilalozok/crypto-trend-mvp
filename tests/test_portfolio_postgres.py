import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.db.models.account import Account
from app.db.models.candidate_observation import CandidateObservation
from app.db.models.candidate_outcome import CandidateOutcome
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


def test_concurrent_candidate_outcomes_are_immutable(sessions, monkeypatch):  # noqa: F811
    from app.db.models.binance_spot import BinanceSpotCandle
    from app.services import candidate_outcomes
    from app.services.formations import BAR

    owner, scan_id = str(uuid4()), str(uuid4())
    entry = 1791226800000 // BAR * BAR
    with sessions() as db:
        db.add(
            Account(
                id=owner, username="outcome-test", password_hash="test", active=True, created_ms=0
            )
        )
        db.flush()
        db.add(
            CandidateScan(
                id=scan_id,
                account_id=owner,
                request_id=str(uuid4()),
                created_ms=entry - BAR,
                rule_hash="test",
                payload=dict(
                    evaluation_entry_ms=entry,
                    candidates=[
                        dict(symbol="BTCUSDT", primary_pattern=dict(name="Test"), evidence_score=80)
                    ],
                ),
            )
        )
        for i in range(4):
            db.add(
                BinanceSpotCandle(
                    symbol="BTCUSDT",
                    interval="15m",
                    open_time=entry + i * BAR,
                    open=100,
                    high=103,
                    low=99,
                    close=102,
                    volume=1,
                )
            )
        db.commit()

    def calculate(_):
        with sessions() as db:
            scan = db.get(CandidateScan, scan_id)
            return candidate_outcomes.results(db, scan, entry + 4 * BAR, persist=True)["candidates"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(calculate, [1, 2]))
    assert results[0] == results[1]
    with sessions() as db:
        assert db.query(CandidateOutcome).filter_by(scan_id=scan_id).count() == 1

    # Exercise PostgreSQL JSON eligibility and idempotent worker settlement.
    from app.services.candidate_auto import settle_due

    monkeypatch.setenv("CANDIDATE_OUTCOMES_ENABLED", "true")
    with sessions() as db:
        assert settle_due(db, entry + 4 * BAR) == 1
        assert db.query(CandidateOutcome).filter_by(scan_id=scan_id).count() == 1


def test_concurrent_live_candidate_observation_is_unique(sessions, monkeypatch):  # noqa: F811
    from app.services import candidate_observer
    from app.services.candidate_archive import archive_hash
    from app.services.formations import BAR

    owner, sid = str(uuid4()), str(uuid4())
    entry = 1791226800000 // BAR * BAR
    with sessions() as db:
        db.add(
            Account(
                id=owner, username="observer-test", password_hash="test", active=True, created_ms=0
            )
        )
        db.flush()
        db.add(
            CandidateScan(
                id=sid,
                account_id=owner,
                request_id=str(uuid4()),
                created_ms=entry,
                rule_hash=archive_hash(),
                payload=dict(evaluation_entry_ms=entry, candidates=[dict(symbol="BTCUSDT")]),
            )
        )
        db.commit()
    monkeypatch.setenv("CANDIDATE_OBSERVATIONS_ENABLED", "true")

    # A missing-data observation still has exactly one immutable record per window.
    def write(_):
        with sessions() as db:
            return candidate_observer.observe_symbol(db, "BTCUSDT", entry + BAR)

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(write, [0, 1]))
    assert sum(counts) == 1
    with sessions() as db:
        assert db.query(CandidateObservation).filter_by(scan_id=sid).count() == 1
