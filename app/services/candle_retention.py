"""Conservative raw-candle retention; never deletes financial or research records."""

from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy import case, delete, func, select, text, tuple_

from app.db.models.account import Purchase
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_outcome import CandidateOutcome
from app.db.models.candidate_scan import CandidateScan
from app.db.models.early_formation import EarlyFormation
from app.db.models.forward_signal import ForwardSignal
from app.db.models.portfolio_timeframe import PortfolioCandle
from app.db.models.simulation import Simulation, SimulationCandle
from app.services.candidate_outcomes import HORIZONS

DAY = 86_400_000
BARS = {"5m": 300_000, "15m": 900_000, "4h": 14_400_000, "1d": DAY}
DEFAULTS = {"15m": 45, "4h": 90, "1d": 400, "5m": 35}
MINIMUMS = {"15m": 35, "4h": 70, "1d": 240, "5m": 7}
MODELS = {
    "15m": BinanceSpotCandle,
    "4h": PortfolioCandle,
    "1d": PortfolioCandle,
    "5m": SimulationCandle,
}
ALLOWED_TABLES = {m.__tablename__ for m in MODELS.values()}


@dataclass(frozen=True)
class Settings:
    days: dict
    batch_size: int = 2000
    max_rows: int = 100000
    seconds: int = 120

    def __post_init__(self):
        if set(self.days) != set(DEFAULTS):
            raise ValueError("Dört mum vadesinin saklama süresi gerekli.")
        if any(type(v) is not int or not MINIMUMS[k] <= v <= 3650 for k, v in self.days.items()):
            raise ValueError("Saklama günleri güvenli alt sınırın dışında.")
        if not 1 <= self.batch_size <= 10000 or not 1 <= self.max_rows <= 1000000:
            raise ValueError("Geçersiz silme sınırı.")
        if not 5 <= self.seconds <= 600:
            raise ValueError("Geçersiz çalışma süresi.")


def time_label(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat() if value is not None else None


def bounds(model, interval):
    return [] if interval == "5m" else [model.interval == interval]


def protect(target, symbol, value):
    if not isinstance(symbol, str) or not symbol or type(value) is not int or value < 0:
        raise ValueError("Korunan kayıtta geçersiz parite / zaman; temizlik durduruldu.")
    target[symbol] = min(target.get(symbol, value), value)


def dependencies(db):
    """Earliest required history per symbol; incomplete outcomes remain pinned indefinitely."""
    historical, execution = {}, {}
    for symbol, at in db.execute(
        select(Purchase.symbol, func.min(Purchase.purchased_ms)).group_by(Purchase.symbol)
    ):
        protect(historical, symbol, at)
    for symbol, at in db.execute(
        select(Simulation.symbol, func.min(Simulation.entry_ms)).group_by(Simulation.symbol)
    ):
        protect(historical, symbol, at)
        # Keep the full promised buy-to-present chart even after sale until tracking is deleted.
        protect(execution, symbol, at)
    for model in (ForwardSignal, EarlyFormation):
        for symbol, at in db.execute(
            select(model.symbol, func.min(model.entry_ms))
            .where(model.complete.is_(False))
            .group_by(model.symbol)
        ):
            protect(historical, symbol, at)
    completed = (
        select(func.count())
        .select_from(CandidateOutcome)
        .where(
            CandidateOutcome.scan_id == CandidateScan.id,
            CandidateOutcome.payload["status"].as_string() == "complete",
        )
        .correlate(CandidateScan)
        .scalar_subquery()
    )
    length = func.json_array_length(CandidateScan.payload["candidates"])
    # Old scans without an entry timestamp never had a prospective measurement window.
    scans = db.execute(
        select(
            CandidateScan.payload["evaluation_entry_ms"].as_string(),
            CandidateScan.payload["candidates"],
        ).where(completed < length * len(HORIZONS))
    ).yield_per(100)
    for at, candidates in scans:
        if at is None:
            continue
        try:
            at = int(at)
        except (ValueError, TypeError):
            raise ValueError("Tarama ölçüm zamanı geçersiz; temizlik durduruldu.") from None
        for candidate in candidates:
            protect(historical, candidate["symbol"], at)
    # A legacy pending exit may need a specific bar from long before the ordinary cutoff.
    for symbol, at, interval in db.execute(
        select(Simulation.symbol, Simulation.exit_ms, Simulation.exit_interval).where(
            Simulation.state == "pending_exit"
        )
    ):
        if interval == "5m":
            protect(execution, symbol, at)
        else:
            protect(historical, symbol, at)
    return historical, execution


def warmup(db, model, interval):
    """Preserve the last 200 available rows for each symbol, including stale / gapped feeds."""
    row_number = (
        func.row_number()
        .over(partition_by=model.symbol, order_by=model.open_time.desc())
        .label("rn")
    )
    ranked = (
        select(model.symbol, model.open_time, row_number).where(*bounds(model, interval)).subquery()
    )
    return dict(
        db.execute(
            select(ranked.c.symbol, func.min(ranked.c.open_time))
            .where(ranked.c.rn <= 200)
            .group_by(ranked.c.symbol)
        ).all()
    )


def predicate(db, interval, stamp, settings, warm):
    model = MODELS[interval]
    cutoff = stamp - settings.days[interval] * DAY
    history, executions = dependencies(db)
    pinned = executions if interval == "5m" else history
    floors = {symbol: min(cutoff, at) for symbol, at in warm.items()}
    lookback = BARS[interval] if interval == "5m" else 200 * BARS[interval]
    for symbol, at in pinned.items():
        floors[symbol] = min(floors.get(symbol, cutoff), max(0, at - lookback))
    actual = case(floors, value=model.symbol, else_=cutoff) if floors else cutoff
    return [*bounds(model, interval), model.open_time < cutoff, model.open_time < actual], pinned


def plan(db, stamp, settings):
    output = []
    for interval, model in MODELS.items():
        warm = warmup(db, model, interval)
        conditions, pinned = predicate(db, interval, stamp, settings, warm)
        count = db.scalar(select(func.count()).select_from(model).where(*conditions))
        oldest = db.scalar(select(func.min(model.open_time)).where(*bounds(model, interval)))
        output.append(
            dict(
                table=model.__tablename__,
                interval=interval,
                keep_days=settings.days[interval],
                cutoff_utc=time_label(stamp - settings.days[interval] * DAY),
                oldest_utc=time_label(oldest),
                deletable_rows=count,
                history_pinned_symbols=len(pinned),
                warmup_symbols=len(warm),
            )
        )
    return output


def apply(factory, stamp, settings, clock=monotonic):
    """Commit small batches. Re-read dependencies for each batch; limits bound catch-up work."""
    start = clock()
    removed = dict.fromkeys(MODELS, 0)
    total = 0
    # Rotate priority so a large 15m backlog cannot starve another timeframe each day.
    intervals = list(MODELS)
    shift = (stamp // DAY) % len(intervals)
    for interval in intervals[shift:] + intervals[:shift]:
        if total >= settings.max_rows or clock() - start >= settings.seconds:
            break
        model = MODELS[interval]
        with factory() as db:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SET LOCAL statement_timeout = '30s'"))
            warm = warmup(db, model, interval)
        while total < settings.max_rows and clock() - start < settings.seconds:
            with factory() as db:
                if db.get_bind().dialect.name == "postgresql":
                    db.execute(text("SET LOCAL lock_timeout = '2s'"))
                    db.execute(text("SET LOCAL statement_timeout = '30s'"))
                conditions, _ = predicate(db, interval, stamp, settings, warm)
                columns = list(model.__table__.primary_key.columns)
                victims = (
                    select(*columns)
                    .where(*conditions)
                    .order_by(*columns)
                    .limit(min(settings.batch_size, settings.max_rows - total))
                )
                operation = delete(model).where(tuple_(*columns).in_(victims))
                changed = db.execute(
                    operation.execution_options(synchronize_session=False)
                ).rowcount
                db.commit()
            removed[interval] += changed
            total += changed
            if changed == 0:
                break
    return dict(
        deleted_rows=total,
        by_interval=removed,
        limit_reached=total >= settings.max_rows or clock() - start >= settings.seconds,
    )


def sizes(connection):
    if connection.dialect.name != "postgresql":
        return dict(note="Canlı tablo boyutları PostgreSQL bağlantısı gerektirir.")
    total = connection.scalar(text("SELECT pg_database_size(current_database())"))
    rows = connection.execute(
        text(
            """
        SELECT schemaname, relname AS table_name,
               pg_total_relation_size(relid) AS total_bytes,
               pg_table_size(relid) AS data_bytes, pg_indexes_size(relid) AS index_bytes,
               n_live_tup AS estimated_live_rows, n_dead_tup AS estimated_dead_rows,
               last_autovacuum::text, last_autoanalyze::text
        FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC
    """
        )
    )
    tables = []
    for row in rows.mappings():
        item = dict(row)
        for field in ("total", "data", "index"):
            item[field + "_mb"] = round(item[field + "_bytes"] / 1024**2, 3)
        tables.append(item)
    return dict(database_bytes=total, database_mb=round(total / 1024**2, 3), tables=tables)


def vacuum(engine, tables):
    """Plain VACUUM only, outside a transaction; table identifiers come from a fixed whitelist."""
    if engine.dialect.name != "postgresql":
        return
    for table in sorted(set(tables)):
        if table not in ALLOWED_TABLES:
            raise ValueError("Bakım için izin verilmeyen tablo.")
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("SET statement_timeout = '120s'"))
            connection.execute(text(f'VACUUM (ANALYZE) "{table}"'))
