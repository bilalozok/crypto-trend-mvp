"""Read the signal cohort behind a public formation summary row."""

import math

from sqlalchemy import func, select

from app.db.models.forward_signal import ForwardSignal
from app.services.formations import timestamp


def listing(db, stamp, days, fingerprint, pattern, hours, offset=0):
    name = func.coalesce(ForwardSignal.snapshot["primary_pattern"]["name"].as_string(), "Unknown")
    query = select(ForwardSignal).where(
        ForwardSignal.rule_hash == fingerprint,
        ForwardSignal.observed_ms >= stamp - days * 86_400_000,
        ForwardSignal.observed_ms <= stamp,
        name == pattern,
    )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(
        query.order_by(
            ForwardSignal.observed_ms.desc(),
            ForwardSignal.symbol,
            ForwardSignal.signal_close_ms.desc(),
        )
        .offset(offset)
        .limit(51)
    ).all()
    signals = []
    for row in rows[:50]:
        outcome = row.outcomes.get(str(hours * 4))
        value = ((outcome or {}).get("result") or {}).get("net_return_pct")
        valid = (
            outcome is not None
            and outcome.get("status") == "complete"
            and type(value) in (int, float)
            and math.isfinite(value)
        )
        signals.append(
            dict(
                symbol=row.symbol,
                observed_at=timestamp(row.observed_ms),
                scheduled_entry_time=timestamp(row.entry_ms),
                evidence_score=row.snapshot.get("evidence_score"),
                status="pending" if outcome is None else "complete" if valid else "invalid_data",
                net_return_pct=value if valid else None,
            )
        )
    return dict(
        pattern_name=pattern,
        hours=hours,
        total_signals=total,
        signals=signals,
        next_offset=offset + 50 if len(rows) > 50 else None,
        note=(
            "Bu coinler seçilen dönem, kural ve formasyonun geçmiş sinyalleridir; "
            "güncel formasyona uyan coin listesi değildir. Sonuçlar en son saklanan "
            "haliyle okunur; özetten sonra tamamlanmış olabilir. "
            "Coin adına basınca güncel grafik açılır."
        ),
    )
