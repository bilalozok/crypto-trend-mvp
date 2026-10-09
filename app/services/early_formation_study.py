"""Prospective forming alerts, immutable context and raw price movement; no orders."""

import hashlib
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from statistics import mean

from sqlalchemy import select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.early_formation import EarlyFormation
from app.db.models.formation_history import FormationEvent, FormationState
from app.services.formation_early import millis
from app.services.formations import BAR, timestamp
from app.services.forward_tracking import primitive
from app.services.indicator_confluence import summarize
from app.services.technical_indicators import analyze_rows

HOURS = (1, 2, 4, 24)
VERSION = "early_formation_measurement_v1"


@lru_cache(maxsize=1)
def rules_hash():
    root = Path(__file__).parent
    paths = set(root.glob("*patterns.py")) | {
        root / name
        for name in (
            "formations.py",
            "formation_early.py",
            "technical_indicators.py",
            "fibonacci_context.py",
            "indicator_confluence.py",
            "early_formation_study.py",
        )
    }
    digest = hashlib.sha256(VERSION.encode())
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def eligible(event, state, stamp):
    p = event.current
    closed = stamp // BAR * BAR
    return (
        event.candle_close_ms == closed
        and closed <= event.observed_ms <= stamp
        and state.candle_close_ms == closed
        and p.get("status") == "forming"
        and p.get("direction") in {"up", "down"}
        and millis(p.get("structure_available_at")) == closed
        and all(
            state.signature.get(k) == p.get(k)
            for k in ("status", "direction", "start_time", "anchor_time")
        )
    )


def record_symbol(db, symbol, stamp):
    from app.services.binance_coverage import STABLECOIN_BASES

    target = db.get(BinanceSpotSymbol, symbol)
    if not target or not target.active or target.base_asset in STABLECOIN_BASES:
        return 0
    events = db.execute(
        select(FormationEvent, FormationState)
        .join(
            FormationState,
            (FormationEvent.symbol == FormationState.symbol)
            & (FormationEvent.pattern == FormationState.pattern),
        )
        .where(
            FormationEvent.symbol == symbol, FormationEvent.candle_close_ms == stamp // BAR * BAR
        )
    ).all()
    pairs = [(e, st) for e, st in events if eligible(e, st, stamp)]
    if not pairs:
        return 0
    rows = db.scalars(
        select(BinanceSpotCandle)
        .where(BinanceSpotCandle.symbol == symbol, BinanceSpotCandle.open_time + BAR <= stamp)
        .order_by(BinanceSpotCandle.open_time.desc())
        .limit(200)
    ).all()[::-1]
    context = analyze_rows(rows, "15m", stamp)
    fingerprint = rules_hash()
    recorded = 0
    for event, _ in pairs:
        key = (symbol, fingerprint, event.pattern, event.candle_close_ms)
        if db.get(EarlyFormation, key):
            continue
        p = event.current
        agreement = summarize(context, [p])
        # At least 15m after this snapshot is actually observed, then the next boundary.
        entry = ((stamp + BAR + BAR - 1) // BAR) * BAR
        saved = dict(
            version=VERSION,
            pattern=p,
            indicator_status=context["status"],
            latest=context.get("latest"),
            confluence=agreement,
            context_observed_at=timestamp(stamp),
            direction=p["direction"],
            name=p.get("name", event.pattern),
            event_observed_at=timestamp(event.observed_ms),
        )
        db.add(
            EarlyFormation(
                symbol=symbol,
                rule_hash=fingerprint,
                pattern=event.pattern,
                close_ms=event.candle_close_ms,
                observed_ms=stamp,
                entry_ms=entry,
                snapshot=primitive(saved),
                outcomes={},
                complete=False,
            )
        )
        recorded += 1
    return recorded


def measure(rows, entry, hours, direction):
    count = hours * 4
    if len(rows) != count or any(r.open_time != entry + i * BAR for i, r in enumerate(rows)):
        return dict(status="invalid_data", reason="Eksik veya aralıklı mum; sıfır sayılmadı.")
    if any(not math.isfinite(v) or v <= 0 for r in rows for v in (r.open, r.close)):
        return dict(status="invalid_data", reason="Geçersiz fiyat; sıfır sayılmadı.")
    change = (rows[-1].close / rows[0].open - 1) * 100
    if not math.isfinite(change):
        return dict(
            status="invalid_data", reason="Hesaplanan hareket sonlu değil; sıfır sayılmadı."
        )
    return dict(
        status="complete",
        price_change_pct=change,
        directional_change_pct=change if direction == "up" else -change,
        reference_open=rows[0].open,
        exit_close=rows[-1].close,
    )


def settle_symbol(db, symbol, stamp):
    settled = 0
    for record in db.scalars(
        select(EarlyFormation).where(
            EarlyFormation.symbol == symbol, EarlyFormation.complete.is_(False)
        )
    ):
        updated = dict(record.outcomes)
        for hours in HOURS:
            key = str(hours)
            end = record.entry_ms + hours * 4 * BAR
            if key in updated or end > stamp:
                continue
            rows = db.scalars(
                select(BinanceSpotCandle)
                .where(
                    BinanceSpotCandle.symbol == symbol,
                    BinanceSpotCandle.open_time >= record.entry_ms,
                    BinanceSpotCandle.open_time < end,
                )
                .order_by(BinanceSpotCandle.open_time)
            ).all()
            # Allow collection to catch up for four bars before declaring missing data.
            if len(rows) < hours * 4 and stamp < end + 4 * BAR:
                continue
            updated[key] = dict(
                measure(rows, record.entry_ms, hours, record.snapshot["direction"]),
                measured_at=timestamp(stamp).isoformat(),
            )
            settled += 1
        record.outcomes = updated
        record.complete = all(str(h) in updated for h in HOURS)
    return settled


def track_symbol(db, symbol, stamp):
    try:
        target = db.scalar(
            select(BinanceSpotSymbol).where(BinanceSpotSymbol.symbol == symbol).with_for_update()
        )
        if target is None:
            return dict(recorded=0, settled=0)
        settled = settle_symbol(db, symbol, stamp)
        recorded = record_symbol(db, symbol, stamp)
        db.commit()
        return dict(recorded=recorded, settled=settled)
    except Exception:
        db.rollback()
        raise


def comparison(records):
    retained, last, excluded = [], {}, 0
    for r in sorted(records, key=lambda r: (r.observed_ms, r.symbol, r.pattern)):
        key = (r.rule_hash, r.symbol)
        if key in last and r.entry_ms < last[key] + 96 * BAR:
            excluded += 1
            continue
        last[key] = r.entry_ms
        retained.append(r)
    buckets = defaultdict(list)
    for r in retained:
        base = (r.rule_hash, r.snapshot["name"], r.snapshot["direction"])
        buckets[(*base, "Tüm uyarılar")].append(r)
        patterns = r.snapshot["confluence"]["patterns"]
        if patterns:
            for g in patterns[0]["groups"]:
                if g["name"].startswith(("Trend", "Momentum")):
                    buckets[(*base, g["name"] + " · " + g["state"])].append(r)
    groups = []
    for (rule, pattern, direction, condition), rows in sorted(buckets.items()):
        paired = [
            r
            for r in rows
            if all(r.outcomes.get(str(h), {}).get("status") == "complete" for h in HOURS)
        ]
        for hours in HOURS:
            results = [r.outcomes[str(hours)] for r in paired]
            groups.append(
                dict(
                    rule_hash=rule,
                    pattern=pattern,
                    direction=direction,
                    condition=condition,
                    hours=hours,
                    retained=len(rows),
                    paired=len(paired),
                    incomplete=len(rows) - len(paired),
                    mean_price_change_pct=(
                        mean(r["price_change_pct"] for r in results) if results else None
                    ),
                    mean_directional_change_pct=(
                        mean(r["directional_change_pct"] for r in results) if results else None
                    ),
                )
            )
    return dict(groups=groups, retained=len(retained), overlap_excluded=excluded)


def report(db, stamp, days=7):
    query = select(EarlyFormation).where(
        EarlyFormation.observed_ms >= stamp - days * 86400000, EarlyFormation.observed_ms <= stamp
    )
    records = db.scalars(
        query.order_by(
            EarlyFormation.observed_ms.desc(), EarlyFormation.symbol, EarlyFormation.pattern
        ).limit(1001)
    ).all()
    truncated = len(records) > 1000
    records = records[:1000]
    recent = [
        dict(
            symbol=r.symbol,
            rule_hash=r.rule_hash,
            name=r.snapshot["name"],
            direction=r.snapshot["direction"],
            observed_at=timestamp(r.observed_ms),
            scheduled_reference_at=timestamp(r.entry_ms),
            indicator_status=r.snapshot["indicator_status"],
            outcomes=r.outcomes,
        )
        for r in records[:20]
    ]
    return dict(
        checked_at=timestamp(stamp),
        version=VERSION,
        days=days,
        records=len(records),
        truncated=truncated,
        recent=recent,
        **comparison(records),
        note="Yeni gözlenen oluşumlar; geçmişe dönük üretim yok. Referans açılış en az 15 dakika "
        "sonra planlanır. Ham fiyat değişimi ve beklenen yöne göre hareket ayrı gösterilir; "
        "komisyon/kayma düşülmez, işlem veya portföy getirisi değildir. Aynı coin/sürümün "
        "24 saat çakışan kayıtları karşılaştırmadan dışlanır; göstergeler örtüşebilir. "
        "Karşılaştırma dört sonucu tamamlanan aynı uyarıları kullanır. "
        "Eksik mumlara dört mum toleransı tanınır; sonra geçersiz sayılır, sıfır yapılmaz.",
    )
