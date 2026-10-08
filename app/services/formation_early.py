"""One closed candle of visibility for newly observable, unconfirmed structures."""

from datetime import datetime

from sqlalchemy import select

from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.formation_history import FormationEvent, FormationState
from app.services.binance_coverage import STABLECOIN_BASES
from app.services.formations import BAR, timestamp


def millis(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return None
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None


def listing(db, stamp):
    closed = stamp // BAR * BAR
    events = db.execute(
        select(FormationEvent, FormationState)
        .join(
            FormationState,
            (FormationState.symbol == FormationEvent.symbol)
            & (FormationState.pattern == FormationEvent.pattern),
        )
        .join(BinanceSpotSymbol, BinanceSpotSymbol.symbol == FormationEvent.symbol)
        .where(
            FormationEvent.candle_close_ms == closed,
            FormationEvent.observed_ms >= closed,
            FormationEvent.observed_ms <= stamp,
            FormationState.candle_close_ms == closed,
            BinanceSpotSymbol.active.is_(True),
            BinanceSpotSymbol.base_asset.not_in(STABLECOIN_BASES),
        )
        .order_by(FormationEvent.symbol, FormationEvent.pattern)
    ).all()
    alerts = []
    for event, state in events:
        current = event.current
        if (
            current.get("status") != "forming"
            or current.get("direction") not in ("up", "down")
            or millis(current.get("structure_available_at")) != closed
            or any(
                state.signature.get(key) != current.get(key)
                for key in ("status", "direction", "start_time", "anchor_time")
            )
        ):
            continue
        alerts.append(
            dict(
                id=f"{event.symbol}:{event.pattern}:{closed}",
                symbol=event.symbol,
                name=current.get("name", event.pattern),
                direction=current["direction"],
                observed_at=timestamp(event.observed_ms),
                candle_close_at=timestamp(closed),
                expires_at=timestamp(closed + BAR),
                expires_ms=closed + BAR,
                confirmation_threshold=current.get("confirmation_threshold"),
                invalidation_level=current.get("invalidation_level"),
            )
        )
    return dict(checked_at=timestamp(stamp), alerts=alerts, window_minutes=15)
