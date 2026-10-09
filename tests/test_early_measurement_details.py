from copy import deepcopy

from app.db.models.early_formation import EarlyFormation
from app.db.session import SessionLocal
from app.services.early_formation_study import rules_hash
from app.services.early_measurement_details import report_with_details


def test_saved_context_is_exact_separate_by_version_and_read_only():
    stamp = 1_800_000_000_000
    frozen = dict(
        name="Dip",
        direction="up",
        indicator_status="ready",
        latest=dict(rsi=44),
        confluence=dict(patterns=[]),
    )
    fingerprint = rules_hash()
    with SessionLocal() as db:
        for rule, rsi in [("old", 44), ("new", 66)]:
            snapshot = deepcopy(frozen)
            snapshot["latest"]["rsi"] = rsi
            db.add(
                EarlyFormation(
                    symbol="BTCUSDT",
                    rule_hash=rule,
                    pattern="dip",
                    close_ms=stamp - 1000,
                    observed_ms=stamp,
                    entry_ms=stamp + 1000,
                    snapshot=snapshot,
                    outcomes={},
                    complete=False,
                )
            )
        db.commit()
        result = report_with_details(db, stamp + 1000)
        values = {r["rule_hash"]: r["saved_context"]["latest"]["rsi"] for r in result["recent"]}
        assert values == {"old": 44, "new": 66}
        result["recent"][0]["saved_context"]["latest"]["rsi"] = 99
        assert sorted(r.snapshot["latest"]["rsi"] for r in db.query(EarlyFormation)) == [44, 66]
        assert not db.dirty and not db.new and not db.deleted
        assert rules_hash() == fingerprint
