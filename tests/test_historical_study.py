import json
from types import SimpleNamespace

import pytest

from app.services.formations import BAR
from app.services.historical_study import aggregate, check_history, paired_period
from tests.test_backtest_causality import confirmed_history


def data(count=216):
    return confirmed_history() + [
        SimpleNamespace(open_time=i * BAR, open=102, close=104, high=105, low=101, volume=1)
        for i in range(200, count)
    ]


def test_real_signal_is_paired_across_horizons_with_costs():
    result = paired_period(data(), "BTCUSDT", 200 * BAR, 216 * BAR)
    identities = []
    for horizon, trades in result["by_horizon"].items():
        assert len(trades) == 1
        trade = trades[0]
        identities.append(
            (trade["symbol"], trade["signal_time"], trade["pattern"], trade["evidence_score"])
        )
        assert (trade["exit_time"] - trade["entry_time"]).total_seconds() == horizon * 900
        assert trade["net_return_pct"] < trade["gross_return_pct"]
    assert identities[0] == identities[1] == identities[2]


def test_period_boundary_excludes_signal_for_every_horizon():
    result = paired_period(data(215), "BTCUSDT", 200 * BAR, 215 * BAR)
    assert all(not trades for trades in result["by_horizon"].values())
    assert result["excluded_boundary_signals"] == 1


@pytest.mark.parametrize("failure", ["missing", "duplicate", "invalid"])
def test_incomplete_or_invalid_history_rejected(failure):
    rows = data()
    if failure == "missing":
        rows.pop(80)
    elif failure == "duplicate":
        rows[80].open_time = rows[79].open_time
    else:
        rows[80].close = float("nan")
    with pytest.raises(ValueError):
        check_history(rows, 200 * BAR, 216 * BAR)


def test_summary_weights_each_signal_and_empty_rates_are_null():
    trades = [dict(name="A", net_return_pct=v) for v in (1, -1, -3)]
    trades += [dict(name="B", net_return_pct=3)]
    result = aggregate(trades)
    assert result["all"]["mean_net_return_pct"] == 0
    assert result["all"]["positive_net_rate_pct"] == 50
    assert result["patterns"]["A"]["signals"] == 3
    assert aggregate([])["all"]["mean_net_return_pct"] is None


def test_real_trade_report_serializes_dates():
    from app.workers.historical_study import json_default

    result = paired_period(data(), "BTCUSDT", 200 * BAR, 216 * BAR)
    encoded = json.dumps(result, default=json_default)
    decoded = json.loads(encoded)
    trade = decoded["by_horizon"]["16"][0]
    assert trade["signal_time"].endswith("+00:00")


def test_cli_reads_dated_snapshot_and_saves_complete_report(monkeypatch, tmp_path):
    from app.db.models.binance_spot import BinanceSpotCandle
    from app.db.session import SessionLocal
    from app.services.formations import timestamp
    from app.workers.historical_study import main

    with SessionLocal() as db:
        db.add_all(
            [BinanceSpotCandle(symbol="BTCUSDT", interval="15m", **vars(row)) for row in data(240)]
        )
        db.commit()
    output = tmp_path / "report.json"
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    monkeypatch.setattr(
        "sys.argv",
        [
            "historical_study",
            "--symbols",
            "BTCUSDT",
            "--start",
            timestamp(200 * BAR).isoformat(),
            "--split",
            timestamp(220 * BAR).isoformat(),
            "--end",
            timestamp(240 * BAR).isoformat(),
            "--output",
            str(output),
        ],
    )
    assert main() == 0
    report = json.loads(output.read_text())
    assert report["periods"]["first_period"]["horizons"]["16"]["all"]["signals"] == 1
    assert report["periods"]["first_period"]["horizons"]["4"]["all"]["signals"] == 1
