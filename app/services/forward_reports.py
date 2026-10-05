"""Persist one consistent database snapshot; saved reports are never refreshed."""

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models.forward_report import ForwardReport
from app.db.models.forward_signal import ForwardSignal
from app.services.formations import timestamp
from app.services.forward_summary import summarize
from app.services.forward_tracking import primitive, rules_hash

MAX_SIGNALS = 10_000


def metadata(row):
    return dict(
        id=row.id,
        created_at=timestamp(row.created_ms),
        days=row.days,
        rule_hash=row.rule_hash,
        total_signals=row.signal_count,
        report_path="/analysis/binance/forward/reports/" + row.id,
        download_path="/analysis/binance/forward/reports/" + row.id + "/download",
    )


def create_report(db, stamp, days=7, request_id=None):
    report_id = request_id or str(uuid4())
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        existing = db.get(ForwardReport, report_id)
        if existing:
            if existing.days != days:
                raise ValueError("This request ID already belongs to a different period")
            result = metadata(existing)
            db.rollback()
            return primitive(result)
        fingerprint = rules_hash()
        summary = summarize(db, stamp, days, fingerprint)
        if summary["total_signals"] > MAX_SIGNALS:
            raise ValueError("Report exceeds 10000 signals; choose a shorter period")
        rows = (
            db.execute(
                select(ForwardSignal)
                .where(
                    ForwardSignal.rule_hash == fingerprint,
                    ForwardSignal.observed_ms >= stamp - days * 86_400_000,
                    ForwardSignal.observed_ms <= stamp,
                )
                .order_by(ForwardSignal.observed_ms.desc(), ForwardSignal.symbol)
            )
            .scalars()
            .all()
        )
        signals = [
            dict(
                symbol=r.symbol,
                rule_hash=r.rule_hash,
                signal_time=timestamp(r.signal_close_ms),
                observed_at=timestamp(r.observed_ms),
                scheduled_entry_time=timestamp(r.entry_ms),
                observation_delay_seconds=(r.observed_ms - r.signal_close_ms) / 1000,
                snapshot=r.snapshot,
                outcomes=r.outcomes,
                complete=r.complete,
            )
            for r in rows
        ]
        if len(signals) != summary["total_signals"]:
            raise ValueError("Snapshot changed; retry report creation")
        payload = primitive(
            dict(
                report_version="stored_forward_report_v1",
                id=report_id,
                created_at=timestamp(stamp),
                summary=summary,
                signals=signals,
                note="Fixed observed snapshot; hypothetical outcomes, not portfolio returns.",
            )
        )
        json.dumps(payload, allow_nan=False)
        row = ForwardReport(
            id=report_id,
            created_ms=stamp,
            days=days,
            rule_hash=fingerprint,
            signal_count=len(signals),
            payload=payload,
        )
        db.add(row)
        result = primitive(metadata(row))
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        existing = db.get(ForwardReport, report_id)
        if existing is None or existing.days != days:
            db.rollback()
            raise
        result = primitive(metadata(existing))
        db.rollback()
        return result
    except Exception:
        db.rollback()
        raise


def list_reports(db, limit=20, offset=0):
    total = db.query(ForwardReport).count()
    # The list reads metadata only; large saved JSON payloads are not loaded.
    rows = db.execute(
        select(
            ForwardReport.id,
            ForwardReport.created_ms,
            ForwardReport.days,
            ForwardReport.rule_hash,
            ForwardReport.signal_count,
        )
        .order_by(ForwardReport.created_ms.desc(), ForwardReport.id)
        .offset(offset)
        .limit(limit)
    ).all()
    following = offset + len(rows)
    return primitive(
        dict(
            version="stored_forward_report_v1",
            total=total,
            limit=limit,
            offset=offset,
            next_offset=following if following < total else None,
            reports=[metadata(row) for row in rows],
        )
    )
