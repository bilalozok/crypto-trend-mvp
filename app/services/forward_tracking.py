"""Observed signals only; hypothetical entry strictly after observation."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.forward_signal import ForwardSignal
from app.services.binance_coverage import STABLECOIN_BASES
from app.services.bullish_candidates import rank_match
from app.services.formations import BAR, analyze, timestamp
from app.services.signal_backtest import outcome

CONFIG = dict(
    version="observed_forward_v1",
    fee_bps=10,
    slippage_bps=5,
    min_volume_ratio=1.5,
    cooldown_bars=16,
    horizons=[4, 8, 16],
    entry_policy="next_15m_boundary_after_observation",
)


@lru_cache(maxsize=1)
def rules_hash():
    root = Path(__file__).parent
    files = set(root.glob("*patterns.py")) | {
        root / "formations.py",
        root / "bullish_candidates.py",
        root / "signal_backtest.py",
        Path(__file__),
    }
    digest = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode())
    for path in sorted(files):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def primitive(value):
    if isinstance(value, dict):
        return {key: primitive(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [primitive(item) for item in value]
    return value.isoformat() if hasattr(value, "isoformat") else value


def save_observation(db, analysis, stamp, fingerprint):
    if analysis is None or analysis["status"] != "ready":
        return 0
    closed = stamp // BAR * BAR
    new = [
        p
        for p in analysis["patterns"]
        if p["status"] == "confirmed"
        and p["direction"] == "up"
        and p["confirmed_at"] == timestamp(closed)
        and p["last_candle_close_time"] == timestamp(closed)
    ]
    if not new:
        return 0
    relevant = new + [p for p in analysis["patterns"] if p["direction"] == "down"]
    candidate, _ = rank_match(
        dict(symbol=analysis["symbol"], quote_volume_24h=0, patterns=relevant),
        max_age=4,
        min_ratio=1.5,
    )
    if candidate is None:
        return 0
    symbol = analysis["symbol"]
    if db.get(ForwardSignal, (symbol, fingerprint, closed)) is not None:
        return 0
    latest = db.scalar(
        select(ForwardSignal)
        .where(
            ForwardSignal.symbol == symbol,
            ForwardSignal.rule_hash == fingerprint,
        )
        .order_by(ForwardSignal.entry_ms.desc())
        .limit(1)
    )
    entry = (stamp // BAR + 1) * BAR
    if latest is not None and entry < latest.entry_ms + 16 * BAR:
        return 0
    snapshot = dict(
        config=dict(CONFIG),
        method_version=analysis["method_version"],
        evidence_score=candidate["evidence_score"],
        score_components=candidate["score_components"],
        primary_pattern=candidate["primary_pattern"],
        supporting_patterns=candidate["supporting_patterns"],
    )
    db.add(
        ForwardSignal(
            symbol=symbol,
            rule_hash=fingerprint,
            signal_close_ms=closed,
            observed_ms=stamp,
            entry_ms=entry,
            snapshot=primitive(snapshot),
            outcomes={},
            complete=False,
        )
    )
    return 1


def settle_pending(db, symbol, stamp):
    settled = 0
    pending = list(
        db.scalars(
            select(ForwardSignal).where(
                ForwardSignal.symbol == symbol,
                ForwardSignal.complete.is_(False),
            )
        )
    )
    for signal in pending:
        updated = dict(signal.outcomes)
        config = signal.snapshot["config"]
        for horizon in config["horizons"]:
            key = str(horizon)
            if key in updated or signal.entry_ms + horizon * BAR > stamp:
                continue
            fields = ("open_time", "open", "high", "low", "close", "volume")
            rows = db.execute(
                select(*[getattr(BinanceSpotCandle, f) for f in fields])
                .where(
                    BinanceSpotCandle.symbol == symbol,
                    BinanceSpotCandle.open_time >= signal.entry_ms,
                    BinanceSpotCandle.open_time < signal.entry_ms + horizon * BAR,
                )
                .order_by(BinanceSpotCandle.open_time)
            ).all()
            preceding = SimpleNamespace(open_time=signal.entry_ms - BAR)
            result, error = outcome(
                [preceding] + list(rows), 0, horizon, config["fee_bps"], config["slippage_bps"]
            )
            updated[key] = primitive(
                dict(
                    status="invalid_data" if error else "complete",
                    measured_at=timestamp(stamp),
                    result=result,
                )
            )
            settled += 1
        signal.outcomes = updated
        signal.complete = all(str(h) in updated for h in config["horizons"])
    return settled


def track_symbol(db, symbol, stamp):
    try:
        target = db.scalar(
            select(BinanceSpotSymbol).where(BinanceSpotSymbol.symbol == symbol).with_for_update()
        )
        if target is None:
            db.rollback()
            return dict(recorded=0, settled=0)
        settled = settle_pending(db, symbol, stamp)
        recorded = 0
        if target.active and target.base_asset not in STABLECOIN_BASES:
            recorded = save_observation(db, analyze(db, symbol, stamp), stamp, rules_hash())
        db.commit()
        return dict(recorded=recorded, settled=settled)
    except Exception:
        db.rollback()
        raise


def listing(db, symbol=None, limit=20, offset=0):
    query = db.query(ForwardSignal)
    if symbol:
        query = query.filter(ForwardSignal.symbol == symbol)
    total = query.count()
    rows = (
        query.order_by(
            ForwardSignal.observed_ms.desc(), ForwardSignal.symbol, ForwardSignal.rule_hash
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return dict(
        version=CONFIG["version"],
        current_rule_hash=rules_hash(),
        total=total,
        limit=limit,
        offset=offset,
        next_offset=offset + len(rows) if offset + len(rows) < total else None,
        signals=[
            dict(
                symbol=r.symbol,
                rule_hash=r.rule_hash,
                signal_time=timestamp(r.signal_close_ms),
                observed_at=timestamp(r.observed_ms),
                observation_delay_seconds=(r.observed_ms - r.signal_close_ms) / 1000,
                scheduled_entry_time=timestamp(r.entry_ms),
                snapshot=r.snapshot,
                outcomes=r.outcomes,
                complete=r.complete,
            )
            for r in rows
        ],
        note=(
            "Observed snapshots and hypothetical candle-open entries; "
            "no orders or portfolio returns."
        ),
    )
