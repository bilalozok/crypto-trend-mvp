from copy import deepcopy
from types import SimpleNamespace

from app.services.candidate_indicator_study import snapshot, study
from app.services.formations import BAR


def candidate(version="v1", state="supports"):
    return dict(
        primary_pattern=dict(name="Dip"),
        indicator_snapshot=dict(
            status="ready",
            version=version,
            primary_context=dict(
                groups=[
                    dict(name="Trend · EMA50", state=state),
                    dict(name="Momentum · RSI / MACD", state=state),
                ]
            ),
        ),
    )


def outcomes(value):
    return [dict(status="complete", net_return_pct=value) for _ in range(4)]


def test_groups_preserve_versions_and_do_not_zero_missing_outcomes():
    records = [
        ("rule", candidate(), outcomes(2)),
        ("rule", candidate(state="conflicts"), outcomes(-2)),
        ("rule", candidate(), [outcomes(9)[0], None, None, None]),
        ("rule", candidate(version="v2"), outcomes(50)),
        ("rule", dict(primary_pattern=dict(name="Dip")), outcomes(99)),
    ]
    original = deepcopy(records)
    d = study(records)
    assert d["excluded_without_snapshot"] == 1
    groups = [g for g in d["groups"] if g["indicator_version"] == "v1"]
    base = next(g for g in groups if g["condition"] == "Tüm gösterge kayıtlı adaylar")
    assert (base["retained"], base["paired"], base["incomplete"]) == (3, 2, 1)
    assert base["horizons"][0]["mean_net_return_pct"] == 0
    support = next(g for g in groups if g["condition"] == "Trend · EMA50 · supports")
    assert support["horizons"][0]["mean_difference_pp"] == 2
    assert records == original
    assert study([])["groups"] == []


def test_snapshot_values_are_copied_at_scan_time_and_no_backfill():
    rows = [
        SimpleNamespace(open_time=i * BAR, close=100 + i, high=101 + i, low=99 + i)
        for i in range(200)
    ]
    p = dict(
        name="Dip",
        direction="up",
        status="confirmed",
        current_confirmation=True,
        report_volume_supported=True,
    )
    saved = snapshot(rows, 200 * BAR, [p], dict(name="Dip"), "v1")
    assert saved["status"] == "ready"
    assert saved["latest"]["close"] == 299
    assert saved["primary_context"]["stage"] == "confirmed"
    rows[-1].close = 500
    assert saved["latest"]["close"] == 299
    assert snapshot(rows, 201 * BAR, [p], dict(name="Dip"), "v1")["status"] == "stale_data"
