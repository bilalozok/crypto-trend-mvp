from copy import deepcopy

from sqlalchemy import select

from app.db.models.early_formation import EarlyFormation
from app.services.early_data_health import data_health
from app.services.early_formation_study import report


def report_with_details(db, stamp, days=7):
    result = report(db, stamp, days)
    result["data_health"] = data_health(db, stamp, days)
    recent = result["recent"]
    if not recent:
        return result
    observed = {round(r["observed_at"].timestamp() * 1000) for r in recent}
    records = db.scalars(select(EarlyFormation).where(EarlyFormation.observed_ms.in_(observed)))
    snapshots = {
        (r.symbol, r.rule_hash, r.observed_ms, r.snapshot["name"]): r.snapshot for r in records
    }
    for row in recent:
        stamp_ms = round(row["observed_at"].timestamp() * 1000)
        snapshot = snapshots.get((row["symbol"], row["rule_hash"], stamp_ms, row["name"]))
        row["saved_context"] = deepcopy(snapshot) if snapshot is not None else None
    return result
