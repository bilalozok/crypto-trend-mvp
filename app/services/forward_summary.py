"""Read-only results grouped by a single frozen rule fingerprint."""

import math
from statistics import mean, median

from sqlalchemy import select

from app.db.models.forward_signal import ForwardSignal
from app.services.formations import timestamp
from app.services.forward_tracking import rules_hash


def summarize(db, stamp, days=7, fingerprint=None):
    fingerprint = fingerprint or rules_hash()
    cutoff = stamp - days * 86_400_000
    rows = db.execute(
        select(
            ForwardSignal.symbol,
            ForwardSignal.observed_ms,
            ForwardSignal.entry_ms,
            ForwardSignal.outcomes,
            ForwardSignal.snapshot["primary_pattern"]["name"].as_string().label("pattern_name"),
            ForwardSignal.snapshot["evidence_score"].as_float().label("score"),
        )
        .where(
            ForwardSignal.rule_hash == fingerprint,
            ForwardSignal.observed_ms >= cutoff,
            ForwardSignal.observed_ms <= stamp,
        )
        .order_by(ForwardSignal.observed_ms.desc(), ForwardSignal.symbol)
    ).all()
    horizons = []
    for bars in (4, 8, 16):
        completed, pending, invalid = [], 0, 0
        for row in rows:
            result = row.outcomes.get(str(bars))
            if result is None:
                pending += 1
                continue
            value = (result.get("result") or {}).get("net_return_pct")
            if (
                result.get("status") != "complete"
                or type(value) not in (int, float)
                or not math.isfinite(value)
            ):
                invalid += 1
            else:
                completed.append(value)
        rate = None
        if completed:
            rate = 100 * sum(value > 0 for value in completed) / len(completed)
        horizons.append(
            dict(
                hours=bars // 4,
                horizon_bars=bars,
                completed=len(completed),
                pending=pending,
                invalid=invalid,
                positive_net_rate_pct=rate,
                mean_net_return_pct=mean(completed) if completed else None,
                median_net_return_pct=median(completed) if completed else None,
            )
        )
    recent = []
    for row in rows[:10]:
        values = {}
        for bars in (4, 8, 16):
            result = row.outcomes.get(str(bars))
            if result is None:
                values[str(bars)] = dict(status="pending", net_return_pct=None)
            else:
                value = (result.get("result") or {}).get("net_return_pct")
                ok = (
                    result.get("status") == "complete"
                    and type(value) in (int, float)
                    and math.isfinite(value)
                )
                values[str(bars)] = dict(
                    status="complete" if ok else "invalid_data",
                    net_return_pct=value if ok else None,
                )
        recent.append(
            dict(
                symbol=row.symbol,
                observed_at=timestamp(row.observed_ms),
                scheduled_entry_time=timestamp(row.entry_ms),
                pattern_name=row.pattern_name,
                evidence_score=row.score,
                outcomes=values,
            )
        )
    return dict(
        version="forward_summary_v1",
        as_of=timestamp(stamp),
        days=days,
        window_start=timestamp(cutoff),
        rule_hash=fingerprint,
        total_signals=len(rows),
        unique_symbols=len({row.symbol for row in rows}),
        horizons=horizons,
        recent_signals=recent,
        note=(
            "Same-rule hypothetical signal outcomes; "
            "pending and invalid results excluded from return statistics."
        ),
    )
