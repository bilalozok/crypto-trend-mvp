"""Paired horizon comparison within fixed, non-overlapping signal periods."""

from collections import defaultdict
from statistics import mean, median

from app.services.formations import BAR, timestamp
from app.services.signal_backtest import outcome, replay, valid

HORIZONS = (4, 8, 16)


def check_history(rows, start, end):
    expected_start = start - 200 * BAR
    expected = (end - expected_start) // BAR
    if start % BAR or end % BAR or start >= end or expected_start < 0:
        raise ValueError("Invalid period")
    if len(rows) != expected or any(
        not valid(row) or row.open_time != expected_start + i * BAR for i, row in enumerate(rows)
    ):
        raise ValueError("Incomplete or invalid historical window")


def paired_period(rows, symbol, start, end, fee_bps=10, slippage_bps=5, min_ratio=1.5):
    check_history(rows, start, end)
    # Select each signal once using the unchanged real walk-forward engine.
    longest = replay(
        rows,
        symbol,
        end,
        horizon=16,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        min_ratio=min_ratio,
    )
    eligible = [
        signal
        for signal in longest["signals"]
        if timestamp(start) <= signal["signal_time"] <= timestamp(end - 16 * BAR)
    ]
    indices = {timestamp(row.open_time + BAR): i for i, row in enumerate(rows)}
    results = {h: [] for h in HORIZONS}
    for signal in eligible:
        index = indices[signal["signal_time"]]
        for horizon in HORIZONS:
            result, error = outcome(rows, index, horizon, fee_bps, slippage_bps)
            if error:
                raise ValueError("Paired horizon outcome unavailable")
            details = {
                key: signal[key]
                for key in (
                    "signal_time",
                    "pattern",
                    "name",
                    "evidence_score",
                    "volume_ratio",
                    "breakout_level",
                )
            }
            results[horizon].append(dict(symbol=symbol, **details, **result))
    excluded = sum(
        signal["signal_time"] > timestamp(end - 16 * BAR)
        for signal in longest["signals"] + longest["pending"]
    )
    return dict(
        by_horizon=results,
        excluded_boundary_signals=excluded,
        window_counts=longest["window_counts"],
    )


def statistics(trades):
    values = [trade["net_return_pct"] for trade in trades]
    positive_rate = None
    if values:
        positive_rate = 100 * sum(v > 0 for v in values) / len(values)
    return dict(
        signals=len(values),
        positive_net_rate_pct=positive_rate,
        mean_net_return_pct=None if not values else mean(values),
        median_net_return_pct=None if not values else median(values),
    )


def aggregate(trades):
    patterns = defaultdict(list)
    for trade in trades:
        patterns[trade["name"]].append(trade)
    return dict(
        all=statistics(trades),
        patterns={name: statistics(values) for name, values in sorted(patterns.items())},
    )
