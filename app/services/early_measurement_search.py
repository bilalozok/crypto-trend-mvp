from copy import deepcopy

from sqlalchemy import select

from app.db.models.early_formation import EarlyFormation
from app.services.formation_early import timestamp


def search_records(
    db,
    stamp,
    *,
    symbol="",
    name="",
    direction="all",
    start_ms=None,
    end_ms=None,
    as_of=None,
    offset=0,
):
    cutoff = stamp if as_of is None else min(as_of, stamp)
    lower = cutoff - 30 * 86400000 if start_ms is None else start_ms
    upper = cutoff + 1 if end_ms is None else min(end_ms, cutoff + 1)
    query = select(EarlyFormation).where(
        EarlyFormation.observed_ms >= lower, EarlyFormation.observed_ms < upper
    )
    if symbol.strip():
        query = query.where(EarlyFormation.symbol == symbol.strip().upper())
    if name.strip():
        query = query.where(EarlyFormation.snapshot["name"].as_string() == name.strip())
    if direction != "all":
        query = query.where(EarlyFormation.snapshot["direction"].as_string() == direction)
    records = db.scalars(
        query.order_by(
            EarlyFormation.observed_ms.desc(),
            EarlyFormation.symbol,
            EarlyFormation.rule_hash,
            EarlyFormation.pattern,
            EarlyFormation.close_ms,
        )
        .offset(offset)
        .limit(21)
    ).all()
    rows = [
        dict(
            symbol=r.symbol,
            rule_hash=r.rule_hash,
            name=r.snapshot["name"],
            direction=r.snapshot["direction"],
            observed_at=timestamp(r.observed_ms),
            scheduled_reference_at=timestamp(r.entry_ms),
            indicator_status=r.snapshot["indicator_status"],
            outcomes=deepcopy(r.outcomes),
            saved_context=deepcopy(r.snapshot),
        )
        for r in records[:20]
    ]
    return dict(
        checked_at=timestamp(stamp),
        as_of=cutoff,
        rows=rows,
        next_offset=offset + 20 if len(records) > 20 else None,
    )
