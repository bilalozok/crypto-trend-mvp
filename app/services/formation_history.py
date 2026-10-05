"""Persist observed transitions only; never reconstruct unobserved history."""

from sqlalchemy import select

from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.formation_history import FormationEvent, FormationState
from app.services.formations import analyze, timestamp

FIELDS = ("status", "direction", "start_time", "anchor_time", "confirmed_at", "breakout_holding")


def primitive(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def save_analysis(db, analysis, observed_ms):
    if analysis is None or analysis["status"] != "ready":
        return 0
    count = 0
    symbol = analysis["symbol"]
    states = {
        s.pattern: s
        for s in db.scalars(select(FormationState).where(FormationState.symbol == symbol))
    }
    for p in analysis["patterns"]:
        closed = round(p["last_candle_close_time"].timestamp() * 1000)
        state = states.get(p["pattern"])
        if state is not None and closed <= state.candle_close_ms:
            continue
        signature = {k: primitive(p.get(k)) for k in FIELDS}
        previous = None if state is None else state.signature
        changed = previous != signature
        if changed and (previous is not None or p["status"] != "not_detected"):
            event_type = "changed"
            if previous is None:
                event_type = "initial_observation"
            elif p["status"] == "not_detected":
                event_type = "disappeared"
            elif previous["status"] == "not_detected":
                event_type = "appeared"
            elif (previous["start_time"], previous["anchor_time"]) != (
                signature["start_time"],
                signature["anchor_time"],
            ):
                event_type = "new_structure"
            db.add(
                FormationEvent(
                    symbol=symbol,
                    pattern=p["pattern"],
                    candle_close_ms=closed,
                    observed_ms=observed_ms,
                    event_type=event_type,
                    previous=previous,
                    current={
                        k: primitive(v)
                        for k, v in p.items()
                        if k not in {"pivot_points", "boundary_lines", "pole_points"}
                    },
                    method_version=analysis["method_version"],
                )
            )
            count += 1
        if state is None:
            db.add(
                FormationState(
                    symbol=symbol, pattern=p["pattern"], candle_close_ms=closed, signature=signature
                )
            )
        else:
            state.candle_close_ms = closed
            state.signature = signature
    return count


def record_symbol(db, symbol, stamp):
    try:
        # Serialize concurrent writers per coin before reading its latest state.
        locked = db.scalar(
            select(BinanceSpotSymbol).where(BinanceSpotSymbol.symbol == symbol).with_for_update()
        )
        if locked is None or not locked.active:
            db.rollback()
            return 0
        count = save_analysis(db, analyze(db, symbol, stamp), stamp)
        db.commit()
        return count
    except Exception:
        db.rollback()
        raise


def history(db, symbol, pattern=None, limit=50, offset=0):
    if db.get(BinanceSpotSymbol, symbol) is None:
        return None
    query = db.query(FormationEvent).filter(FormationEvent.symbol == symbol)
    if pattern is not None:
        query = query.filter(FormationEvent.pattern == pattern)
    total = query.count()
    rows = (
        query.order_by(FormationEvent.candle_close_ms.desc(), FormationEvent.pattern)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return dict(
        exchange="binance",
        market="spot",
        interval="15m",
        symbol=symbol,
        pattern=pattern,
        total=total,
        limit=limit,
        offset=offset,
        next_offset=offset + len(rows) if offset + len(rows) < total else None,
        history_version="observed_transitions_v1",
        note=(
            "İlk kayıt başlangıç gözlemidir. Zamanlar gözlem ve veri zamanıdır; "
            "kayıt öncesi veya atlanan mumlar için olay üretilmez."
        ),
        events=[
            dict(
                pattern=r.pattern,
                candle_close_time=timestamp(r.candle_close_ms),
                observed_at=timestamp(r.observed_ms),
                event_type=r.event_type,
                previous=r.previous,
                current=r.current,
                method_version=r.method_version,
            )
            for r in rows
        ],
    )
