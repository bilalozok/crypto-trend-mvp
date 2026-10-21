from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.db.models.account import Account, Purchase
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_outcome import CandidateOutcome
from app.db.models.candidate_scan import CandidateScan
from app.db.models.early_formation import EarlyFormation
from app.db.models.forward_signal import ForwardSignal
from app.db.models.portfolio_timeframe import PortfolioCandle
from app.db.models.real_gainer import RealGainerStudy
from app.db.models.simulation import Simulation, SimulationCandle
from app.db.session import SessionLocal
from app.services import candle_retention as retain

STAMP = 2000 * retain.DAY


def seed(db, model, symbol, begin, interval, count=210):
    values = [
        dict(
            symbol=symbol,
            open_time=begin + i * retain.BARS[interval],
            open=10,
            high=11,
            low=9,
            close=10,
            volume=1,
        )
        for i in range(count)
    ]
    if interval != "5m":
        for value in values:
            value["interval"] = interval
    db.add_all([model(**v) for v in values])


def owner(db):
    db.add(
        Account(id="owner", username="retain_test", password_hash="hash", active=True, created_ms=1)
    )
    db.flush()


def tracked(db, symbol, state="closed", entry=None):
    at = entry if entry is not None else STAMP - 100 * retain.DAY
    db.add(
        Simulation(
            id=symbol,
            account_id="owner",
            symbol=symbol,
            created_ms=at,
            state=state,
            source="manual",
            mode="paper",
            entry_ms=at,
            entry_interval="5m",
            exit_interval="5m",
            request={},
            selection={},
            observations=[],
        )
    )


def test_readonly_plan_batch_limits_exact_boundary_and_last_200():
    settings = retain.Settings(dict(retain.DEFAULTS), batch_size=3, max_rows=4)
    begin = STAMP - 100 * retain.DAY
    with SessionLocal() as db:
        for interval, model in retain.MODELS.items():
            # Daily rows must be older than the 400d cutoff before any is eligible.
            origin = STAMP - 700 * retain.DAY if interval == "1d" else begin
            seed(db, model, "OLD", origin, interval)
        # Exact cutoff survives, and last 200 survive even for stale / gapped feeds.
        cutoff = STAMP - settings.days["15m"] * retain.DAY
        seed(db, BinanceSpotCandle, "EDGE", cutoff, "15m", count=1)
        db.commit()
        report = retain.plan(db, STAMP, settings)
        assert {row["interval"]: row["deletable_rows"] for row in report} == dict.fromkeys(
            retain.MODELS, 10
        )
        assert db.scalar(select(func.count()).select_from(BinanceSpotCandle)) == 211
    result = retain.apply(SessionLocal, STAMP, settings)
    assert result["deleted_rows"] == 4 and result["limit_reached"]
    assert sum(result["by_interval"].values()) == 4
    # Catch-up drains only the ten excess rows per timeframe; repeat is idempotent.
    result = retain.apply(SessionLocal, STAMP, replace(settings, max_rows=100))
    assert result["deleted_rows"] == 36
    assert retain.apply(SessionLocal, STAMP, settings)["deleted_rows"] == 0
    with SessionLocal() as db:
        assert db.get(BinanceSpotCandle, ("EDGE", cutoff)) is not None


def test_financial_graphs_and_pending_research_are_pinned_without_deleting_archives():
    begin = STAMP - 100 * retain.DAY
    settings = retain.Settings(dict(retain.DEFAULTS))
    with SessionLocal() as db:
        owner(db)
        # Each dependency protects an old series which ordinary age cleanup would remove.
        for name in ["SOLD", "BUY", "FORWARD", "EARLY", "CANDIDATE", "FREE"]:
            seed(db, BinanceSpotCandle, name, begin, "15m")
        seed(db, SimulationCandle, "SOLD", begin, "5m")
        tracked(db, "SOLD")
        db.add(
            Purchase(
                id="buy",
                account_id="owner",
                symbol="BUY",
                purchased_ms=begin,
                currency="USDT",
                unit_price="10",
                quantity="2",
                fee="0",
                note="",
                created_ms=1,
            )
        )
        db.add(
            ForwardSignal(
                symbol="FORWARD",
                rule_hash="rule",
                signal_close_ms=begin,
                observed_ms=begin,
                entry_ms=begin,
                snapshot={},
                outcomes={},
                complete=False,
            )
        )
        db.add(
            EarlyFormation(
                symbol="EARLY",
                rule_hash="rule",
                pattern="test",
                close_ms=begin,
                observed_ms=begin,
                entry_ms=begin,
                snapshot={},
                outcomes={},
                complete=False,
            )
        )
        db.add(
            CandidateScan(
                id="scan",
                account_id="owner",
                request_id="request",
                created_ms=begin,
                rule_hash="rule",
                payload={"evaluation_entry_ms": begin, "candidates": [{"symbol": "CANDIDATE"}]},
            )
        )
        db.add(
            RealGainerStudy(
                id="study", day="2026-01-01", version="v", computed_ms=begin, payload={"keep": True}
            )
        )
        db.commit()
    report = retain.apply(SessionLocal, STAMP, settings)
    assert report["deleted_rows"] == 10
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(BinanceSpotCandle)
                .where(BinanceSpotCandle.symbol == "SOLD")
            )
            == 210
        )
        assert db.scalar(select(func.count()).select_from(SimulationCandle)) == 210
        assert db.get(Simulation, "SOLD").state == "closed"
        assert db.get(Purchase, "buy").quantity == "2"
        assert db.get(RealGainerStudy, "study").payload == {"keep": True}
        # Once all four candidate outcomes are stored, only warmup history remains pinned.
        for horizon in (4, 8, 16, 96):
            db.add(
                CandidateOutcome(
                    scan_id="scan",
                    symbol="CANDIDATE",
                    horizon_bars=horizon,
                    payload={"status": "complete"},
                )
            )
        db.delete(db.get(Simulation, "SOLD"))
        db.commit()
    report = retain.apply(SessionLocal, STAMP, settings)
    assert report["deleted_rows"] == 30  # SOLD 15m + 5m and completed candidate history.


def test_pending_legacy_exit_bars_and_long_timeframe_warmup():
    settings = retain.Settings(dict(retain.DEFAULTS))
    begin = STAMP - 700 * retain.DAY
    with SessionLocal() as db:
        owner(db)
        tracked(db, "LEGACY", state="pending_exit", entry=STAMP - 10 * retain.DAY)
        row = db.get(Simulation, "LEGACY")
        db.flush()
        row = db.get(Simulation, "LEGACY")
        row.exit_ms = begin + retain.BARS["15m"]
        row.exit_interval = "15m"
        seed(db, BinanceSpotCandle, "LEGACY", begin, "15m")
        seed(db, PortfolioCandle, "LEGACY", begin, "1d")
        db.commit()
    retain.apply(SessionLocal, STAMP, settings)
    with SessionLocal() as db:
        assert db.get(BinanceSpotCandle, ("LEGACY", begin)) is not None
        # The pending historical context retains the 200 bars before the exit too.
        assert db.get(PortfolioCandle, ("LEGACY", "1d", begin)) is not None


def test_invalid_days_fail_closed_and_vacuum_identifier_whitelist():
    for interval in retain.DEFAULTS:
        days = dict(retain.DEFAULTS)
        days[interval] = retain.MINIMUMS[interval] - 1
        with pytest.raises(ValueError):
            retain.Settings(days)
    with pytest.raises(ValueError):
        retain.Settings(dict(retain.DEFAULTS), max_rows=0)

    class FakeEngine:
        class dialect:
            name = "postgresql"

    with pytest.raises(ValueError):
        retain.vacuum(FakeEngine(), ["private_purchases; DROP TABLE anything"])


def test_time_budget_prevents_starting_a_new_batch():
    with SessionLocal() as db:
        seed(db, BinanceSpotCandle, "TIME", STAMP - 100 * retain.DAY, "15m")
        db.commit()
    moments = iter([0, 100])
    result = retain.apply(
        SessionLocal,
        STAMP,
        retain.Settings(dict(retain.DEFAULTS), seconds=5),
        clock=lambda: next(moments, 100),
    )
    assert result["deleted_rows"] == 0 and result["limit_reached"]


def test_postgres_readonly_sizes_and_candidate_retention():
    import os

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base

    if not os.getenv("TEST_POSTGRES_URL"):
        pytest.skip("TEST_POSTGRES_URL yalnız PostgreSQL CI ortamında tanımlı")
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            owner(db)
            begin = STAMP - 100 * retain.DAY
            seed(db, BinanceSpotCandle, "PGFREE", begin, "15m")
            seed(db, BinanceSpotCandle, "PGPIN", begin, "15m")
            db.add(
                CandidateScan(
                    id="pgscan",
                    account_id="owner",
                    request_id="pgrequest",
                    created_ms=begin,
                    rule_hash="rule",
                    payload={"evaluation_entry_ms": begin, "candidates": [{"symbol": "PGPIN"}]},
                )
            )
            db.commit()
        with factory() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            report = retain.sizes(db.connection())
            assert report["database_bytes"] > 0
            assert any(r["table_name"] == "binance_spot_candles" for r in report["tables"])
            assert (
                retain.plan(db, STAMP, retain.Settings(dict(retain.DEFAULTS)))[0]["deletable_rows"]
                == 10
            )
        assert (
            retain.apply(factory, STAMP, retain.Settings(dict(retain.DEFAULTS)))["deleted_rows"]
            == 10
        )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_worker_default_is_readonly_and_destructive_flags_are_explicit(monkeypatch, capsys):
    import json

    from app.workers import candle_retention_worker as worker

    monkeypatch.setattr(worker, "load_settings", lambda: None)
    with SessionLocal() as db:
        seed(db, BinanceSpotCandle, "REPORT", STAMP - 100 * retain.DAY, "15m")
        db.commit()
    monkeypatch.setattr("app.services.binance_collection.now_ms", lambda: STAMP)
    assert worker.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "read_only"
    assert report["retention_plan"][0]["deletable_rows"] == 10
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BinanceSpotCandle)) == 210
    for flags in (["--vacuum"], ["--sizes-only", "--apply"]):
        with pytest.raises(SystemExit) as exc:
            worker.main(flags)
        assert exc.value.code == 2
