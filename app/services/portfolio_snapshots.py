"""Immutable manual observations and comparable changes, without rewriting daily records."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models.portfolio_observation import PortfolioObservation
from app.db.models.portfolio_snapshot import PortfolioSnapshot
from app.services import portfolio_technical as technical
from app.services.formations import timestamp


def compare(previous, current):
    if previous is None:
        return ["İlk saatli gözlem; karşılaştırılacak önceki kayıt yok."]
    if previous.get("version") != current.get("version"):
        return ["Analiz sürümü farklı; teknik değişim karşılaştırılmadı."]
    notes = []
    old_horizons = {h["interval"]: h for h in previous.get("horizons", [])}
    for h in current.get("horizons", []):
        old = old_horizons.get(h["interval"])
        prefix = h["name"] + ": "
        if old is None or old["status"] != "ready" or h["status"] != "ready":
            notes.append(prefix + "veri hazır değil veya önceki veri eksik; karşılaştırılmadı.")
            continue
        before = {p["pattern"]: p for p in old["patterns"]}
        after = {p["pattern"]: p for p in h["patterns"]}
        changes = []
        for key in sorted(before.keys() | after.keys()):
            a, b = before.get(key), after.get(key)
            name = (b or a)["name"]
            if b and b["current_confirmation"] and not (a and a["current_confirmation"]):
                changes.append(name + ": yeni güncel teyit (" + b["direction"] + ")")
            elif a and a["current_confirmation"] and not (b and b["current_confirmation"]):
                changes.append(name + ": güncel teyit kayboldu")
            if b and b["status"] == "invalidated" and a and a["status"] != "invalidated":
                changes.append(name + ": yapı geçersizleşti")
            if a and b and a["current_confirmation"] and b["current_confirmation"]:
                if a.get("report_volume_supported") != b.get("report_volume_supported"):
                    changes.append(name + ": hacim desteği değişti")
                if a.get("direction") != b.get("direction"):
                    changes.append(name + ": teyit yönü değişti")
            if (
                a
                and b
                and any(
                    a.get(k) != b.get(k)
                    for k in ("breakout_level", "confirmation_threshold", "invalidation_level")
                )
            ):
                changes.append(name + ": fiyat koşulları değişti")
        if old["assessment"] != h["assessment"]:
            changes.insert(0, old["label"] + " → " + h["label"])
        notes.append(prefix + ("; ".join(changes) or "izlenen teyit ve koşullar aynı"))
    return notes


def output(row):
    return dict(
        id=row.id,
        observed_at=timestamp(row.observed_ms),
        symbol=row.symbol,
        technical=row.payload,
        comparison=row.comparison,
    )


def save(db, owner, symbol, request_id, stamp, metadata=None):
    condition = (PortfolioSnapshot.account_id == owner, PortfolioSnapshot.request_id == request_id)
    existing = db.scalar(select(PortfolioSnapshot).where(*condition))
    if existing:
        return dict(created=False, snapshot=output(existing))
    payload = technical.technical(db, symbol, stamp)
    if payload["status"] != "ready":
        return dict(created=False, reason="not_ready", current=payload)
    candidates = []
    for model in (PortfolioSnapshot, PortfolioObservation):
        row = db.scalar(
            select(model)
            .where(
                model.account_id == owner,
                model.symbol == symbol,
                model.observed_ms <= stamp,
            )
            .order_by(model.observed_ms.desc(), model.id.desc())
            .limit(1)
        )
        if row:
            candidates.append(row)
    prior = max(candidates, key=lambda r: r.observed_ms) if candidates else None
    comparison = dict(
        previous_at=timestamp(prior.observed_ms) if prior else None,
        notes=compare(prior.payload if prior else None, payload),
    )
    comparison.update(metadata or {"source": "manual"})
    from uuid import uuid4

    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    inserted = db.execute(
        insert(PortfolioSnapshot)
        .values(
            id=str(uuid4()),
            account_id=owner,
            symbol=symbol,
            request_id=request_id,
            observed_ms=stamp,
            method=technical.VERSION,
            payload=payload,
            comparison=technical.json_ready(comparison),
        )
        .on_conflict_do_nothing(index_elements=["account_id", "request_id"])
        .returning(PortfolioSnapshot.id)
    ).scalar_one_or_none()
    db.commit()
    row = db.scalar(select(PortfolioSnapshot).where(*condition))
    return dict(created=inserted is not None, snapshot=output(row))
