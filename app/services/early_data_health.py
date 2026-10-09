from sqlalchemy import func, select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.early_formation import EarlyFormation
from app.services.formation_early import timestamp
from app.services.formations import BAR


def measurement_health(records, stamp):
    counts = dict(complete=0, invalid=0, waiting=0, awaiting_collection=0, overdue=0)
    delayed = []
    for record in records:
        for hours in (1, 2, 4, 24):
            outcome = record.outcomes.get(str(hours))
            end = record.entry_ms + hours * 4 * BAR
            if outcome:
                counts["complete" if outcome.get("status") == "complete" else "invalid"] += 1
            elif stamp < end:
                counts["waiting"] += 1
            elif stamp < end + 4 * BAR:
                counts["awaiting_collection"] += 1
            else:
                counts["overdue"] += 1
                delayed.append(
                    dict(
                        symbol=record.symbol,
                        name=record.snapshot["name"],
                        hours=hours,
                        expected_at=timestamp(end),
                        rule_hash=record.rule_hash,
                    )
                )
    return dict(**counts, delayed=sorted(delayed, key=lambda r: r["expected_at"])[:10])


def data_health(db, stamp, days):
    expected = stamp // BAR * BAR
    latest = (
        select(BinanceSpotCandle.symbol, func.max(BinanceSpotCandle.open_time).label("opened"))
        .where(BinanceSpotCandle.open_time + BAR <= stamp)
        .group_by(BinanceSpotCandle.symbol)
        .subquery()
    )
    rows = db.execute(
        select(BinanceSpotSymbol.symbol, latest.c.opened, BinanceSpotSymbol.last_success_ms)
        .outerjoin(latest, latest.c.symbol == BinanceSpotSymbol.symbol)
        .where(BinanceSpotSymbol.active.is_(True))
    ).all()
    current = sum(opened is not None and opened + BAR == expected for _, opened, _ in rows)
    missing = sum(opened is None for _, opened, _ in rows)
    last_success = max(
        (success for _, _, success in rows if success is not None and success <= stamp),
        default=None,
    )
    records = db.scalars(
        select(EarlyFormation)
        .where(
            EarlyFormation.observed_ms >= stamp - days * 86400000,
            EarlyFormation.observed_ms <= stamp,
        )
        .order_by(EarlyFormation.observed_ms.desc(), EarlyFormation.symbol, EarlyFormation.pattern)
        .limit(1001)
    ).all()
    return dict(
        checked_at=timestamp(stamp),
        expected_close_at=timestamp(expected),
        active_symbols=len(rows),
        current=current,
        stale=len(rows) - current - missing,
        missing=missing,
        last_success_at=timestamp(last_success) if last_success is not None else None,
        measurements=measurement_health(records[:1000], stamp),
        measurement_records=min(len(records), 1000),
        truncated=len(records) > 1000,
    )
