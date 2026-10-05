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


def test_pattern_groups_reconcile_with_totals_and_preserve_missing_results():
    with SessionLocal() as db:
        add(db, "a" * 64, outcomes={"4": dict(status="complete", result=dict(net_return_pct=2))})
        add(
            db,
            "a" * 64,
            observed=191 * BAR,
            outcomes={"4": dict(status="complete", result=dict(net_return_pct=-1))},
        )
        add(
            db,
            "a" * 64,
            observed=192 * BAR,
            outcomes={"4": dict(status="invalid_data", result=None)},
        )
        row = db.get(ForwardSignal, ("BTCUSDT", "a" * 64, 192 * BAR))
        row.snapshot = dict(primary_pattern=dict(name="Alçalan takoz"), evidence_score=90)
        db.commit()
        result = summarize(db, 200 * BAR, fingerprint="a" * 64)
        groups = {g["pattern_name"]: g for g in result["pattern_groups"]}
        first = groups["Çift dip"]["horizons"][0]
        assert first["completed"] == 2
        assert first["mean_net_return_pct"] == 0.5
        assert first["median_net_return_pct"] == 0.5
        assert first["positive_net_rate_pct"] == 50
        assert groups["Çift dip"]["unique_symbols"] == 1
        other = groups["Alçalan takoz"]["horizons"][0]
        assert other["invalid"] == 1 and other["mean_net_return_pct"] is None
        for index, total in enumerate(result["horizons"]):
            for key in ("completed", "pending", "invalid"):
                assert sum(g["horizons"][index][key] for g in groups.values()) == total[key]
        assert sum(g["total_signals"] for g in groups.values()) == result["total_signals"]


def test_empty_pattern_groups(client):
    with SessionLocal() as db:
        assert summarize(db, 200 * BAR, fingerprint="a" * 64)["pattern_groups"] == []
    assert 'id="forward-patterns"' in client.get("/analysis/binance/dashboard").text


def test_paired_comparison_uses_identical_complete_cohort():
    def outcomes(values):
        return {
            str(b): dict(status="complete", result=dict(net_return_pct=v))
            for b, v in zip((4, 8, 16), values, strict=True)
        }

    with SessionLocal() as db:
        add(db, "a" * 64, outcomes=outcomes((1, -2, 3)))
        add(db, "a" * 64, observed=191 * BAR, outcomes=outcomes((-1, 4, 5)))
        add(
            db,
            "a" * 64,
            observed=192 * BAR,
            outcomes={"4": dict(status="complete", result=dict(net_return_pct=100))},
        )
        add(db, "a" * 64, observed=193 * BAR, outcomes=outcomes((9, 9, "bad")))
        result = summarize(db, 200 * BAR, fingerprint="a" * 64)
        paired = result["paired_comparison"]
        assert paired["total_signals"] == 2 and paired["excluded_signals"] == 2
        assert paired["unique_symbols"] == 1
        assert [h["completed"] for h in paired["horizons"]] == [2, 2, 2]
        assert [h["mean_net_return_pct"] for h in paired["horizons"]] == [0, 1, 4]
        assert [h["median_net_return_pct"] for h in paired["horizons"]] == [0, 1, 4]
        assert [h["positive_net_rate_pct"] for h in paired["horizons"]] == [50, 50, 100]
        assert result["horizons"][0]["completed"] == 4
        assert all(h["pending"] == h["invalid"] == 0 for h in paired["horizons"])


def test_paired_without_fully_settled_signals_has_no_return_statistics(client):
    with SessionLocal() as db:
        add(db, "a" * 64)
        result = summarize(db, 200 * BAR, fingerprint="a" * 64)["paired_comparison"]
        assert result["total_signals"] == 0 and result["excluded_signals"] == 1
        assert all(h["mean_net_return_pct"] is None for h in result["horizons"])
    assert 'id="forward-paired"' in client.get("/analysis/binance/dashboard").text
