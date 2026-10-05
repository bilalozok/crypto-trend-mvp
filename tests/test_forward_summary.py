from app.db.models.forward_signal import ForwardSignal
from app.db.session import SessionLocal
from app.services.formations import BAR
from app.services.forward_summary import summarize


def add(db, key, observed=190 * BAR, outcomes=None):
    db.add(
        ForwardSignal(
            symbol="BTCUSDT",
            rule_hash=key,
            signal_close_ms=observed,
            observed_ms=observed,
            entry_ms=observed + BAR,
            snapshot=dict(primary_pattern=dict(name="Çift dip"), evidence_score=80),
            outcomes=outcomes or {},
            complete=False,
        )
    )
    db.commit()


def test_completed_pending_and_invalid_are_separate():
    with SessionLocal() as db:
        add(
            db,
            "a" * 64,
            outcomes={
                "4": dict(status="complete", result=dict(net_return_pct=2)),
                "8": dict(status="invalid_data", result=None),
            },
        )
        result = summarize(db, 200 * BAR, fingerprint="a" * 64)
        first, second, third = result["horizons"]
        assert (first["completed"], first["pending"], first["invalid"]) == (1, 0, 0)
        assert first["mean_net_return_pct"] == 2
        assert first["positive_net_rate_pct"] == 100
        assert second["invalid"] == 1 and second["mean_net_return_pct"] is None
        assert third["pending"] == 1 and third["mean_net_return_pct"] is None
        assert result["recent_signals"][0]["outcomes"]["16"]["status"] == "pending"


def test_summary_does_not_mix_rules_or_outside_observation_window():
    with SessionLocal() as db:
        add(db, "a" * 64)
        add(db, "b" * 64)
        add(db, "a" * 64, observed=10 * BAR)
        add(db, "a" * 64, observed=201 * BAR)
        result = summarize(db, 200 * BAR, days=1, fingerprint="a" * 64)
        assert result["total_signals"] == 1
        assert result["unique_symbols"] == 1


def test_empty_summary_has_null_rates_and_no_recent_records():
    with SessionLocal() as db:
        result = summarize(db, 200 * BAR, fingerprint="a" * 64)
        assert result["total_signals"] == 0
        assert result["recent_signals"] == []
        assert all(
            h["positive_net_rate_pct"] is None and h["mean_net_return_pct"] is None
            for h in result["horizons"]
        )


def test_malformed_completed_outcome_is_invalid():
    with SessionLocal() as db:
        add(db, "a" * 64, outcomes={"4": dict(status="complete", result=dict(net_return_pct="2"))})
        result = summarize(db, 200 * BAR, fingerprint="a" * 64)
        assert result["horizons"][0]["invalid"] == 1
        assert result["recent_signals"][0]["outcomes"]["4"]["status"] == "invalid_data"


def test_api_and_dashboard(client, monkeypatch):
    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
    monkeypatch.setattr("app.services.forward_summary.rules_hash", lambda: "a" * 64)
    with SessionLocal() as db:
        add(db, "a" * 64)
    result = client.get("/analysis/binance/forward/summary").json()
    assert result["total_signals"] == 1
    assert result["rule_hash"] == "a" * 64
    assert client.get("/analysis/binance/forward/summary?days=0").status_code == 422
    assert client.get("/analysis/binance/forward/summary?rule_hash=bad").status_code == 422
    html = client.get("/analysis/binance/dashboard").text
    assert 'id="forward-load"' in html
    assert "forward/summary?days=7" in html
