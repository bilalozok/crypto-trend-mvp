from app.db.models.forward_signal import ForwardSignal
from app.db.session import SessionLocal
from app.services.formations import BAR
from app.services.forward_pattern_signals import listing

STAMP = 200 * BAR
RULE = "a" * 64


def add(db, n, pattern="Çift dip", rule=RULE, observed=STAMP, outcomes=None):
    db.add(
        ForwardSignal(
            symbol="BTCUSDT" if n % 2 else "ETHUSDT",
            rule_hash=rule,
            signal_close_ms=n,
            observed_ms=observed,
            entry_ms=observed + BAR,
            snapshot=dict(primary_pattern=dict(name=pattern), evidence_score=80),
            outcomes=outcomes or {},
            complete=False,
        )
    )


def test_cohort_pagination_and_result_classification():
    with SessionLocal() as db:
        for n in range(55):
            add(db, n, outcomes={"4": dict(status="complete", result=dict(net_return_pct=-1))})
        add(db, 56, rule="b" * 64)
        add(db, 57, pattern="Çift tepe")
        add(db, 58, observed=STAMP + 1)
        add(db, 59, observed=STAMP - 86_400_001)
        add(db, 60, outcomes={"4": dict(status="invalid_data", result=None)})
        add(db, 61)
        db.commit()
        first = listing(db, STAMP, 1, RULE, "Çift dip", 1)
        assert first["total_signals"] == 57 and first["next_offset"] == 50
        second = listing(db, STAMP, 1, RULE, "Çift dip", 1, 50)
        assert len(second["signals"]) == 7 and second["next_offset"] is None
        rows = first["signals"] + second["signals"]
        assert sum(r["status"] == "complete" for r in rows) == 55
        assert sum(r["status"] == "pending" for r in rows) == 1
        assert sum(r["status"] == "invalid_data" for r in rows) == 1
        assert all(r["net_return_pct"] is None for r in rows if r["status"] != "complete")
        assert db.query(ForwardSignal).count() == 61


def test_public_endpoint_validation_and_exact_selection(client, monkeypatch):
    monkeypatch.setattr("app.main.now_ms", lambda: STAMP)
    with SessionLocal() as db:
        add(db, 1)
        db.commit()
    params = dict(pattern="Çift dip", hours=1, days=7, rule_hash=RULE, as_of_ms=STAMP)
    path = "/analysis/binance/forward/pattern-signals"
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    assert response.json()["total_signals"] == 1
    for changes in [
        dict(hours=3),
        dict(days=31),
        dict(rule_hash="bad"),
        dict(offset=-1),
        dict(as_of_ms=STAMP + 1),
    ]:
        assert client.get(path, params={**params, **changes}).status_code == 422
