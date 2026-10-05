"""Walk-forward signal outcomes using only each decision's closed window."""

import math
from collections import Counter
from statistics import mean, median

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.services.bullish_candidates import rank_match
from app.services.formations import BAR, analyze_rows, timestamp


def valid(row):
    return (
        row.open_time % BAR == 0
        and all(math.isfinite(v) and v > 0 for v in (row.open, row.high, row.low, row.close))
        and math.isfinite(row.volume)
        and row.volume >= 0
        and row.low <= min(row.open, row.close)
        and row.high >= max(row.open, row.close)
    )


def outcome(rows, index, horizon, fee_bps, slippage_bps):
    future = rows[index + 1 : index + 1 + horizon]
    expected = rows[index].open_time + BAR
    if any(not valid(r) or r.open_time != expected + j * BAR for j, r in enumerate(future)):
        return None, "invalid_forward_data"
    if len(future) < horizon:
        return None, "pending_forward_data"
    entry, exit_price = future[0].open, future[-1].close
    fee, slip = fee_bps / 10000, slippage_bps / 10000
    net = exit_price * (1 - slip) * (1 - fee) / (entry * (1 + slip) * (1 + fee)) - 1
    return (
        dict(
            entry_time=timestamp(future[0].open_time),
            entry_open=entry,
            exit_time=timestamp(future[-1].open_time + BAR),
            exit_close=exit_price,
            gross_return_pct=(exit_price / entry - 1) * 100,
            net_return_pct=net * 100,
            best_excursion_pct=(max(r.high for r in future) / entry - 1) * 100,
            worst_excursion_pct=(min(r.low for r in future) / entry - 1) * 100,
        ),
        None,
    )


def replay(rows, symbol, stamp, horizon=8, fee_bps=10, slippage_bps=5, min_ratio=1.5):
    counts = Counter()
    trades = []
    pending = []
    for index in range(199, len(rows)):
        window = rows[index - 199 : index + 1]
        decision = rows[index].open_time + BAR
        analysis = analyze_rows(window, symbol, decision)
        counts[analysis["status"]] += 1
        if analysis["status"] != "ready":
            continue
        new = [
            p
            for p in analysis["patterns"]
            if p["status"] == "confirmed"
            and p["direction"] == "up"
            and p["confirmed_at"] == timestamp(decision)
        ]
        if not new:
            continue
        # Keep all current down evidence, but only newly observed up confirmations.
        relevant = new + [p for p in analysis["patterns"] if p["direction"] == "down"]
        candidate, reason = rank_match(
            dict(symbol=symbol, quote_volume_24h=0, patterns=relevant),
            max_age=4,
            min_ratio=min_ratio,
        )
        if candidate is None:
            counts[reason] += 1
            continue
        counts["qualified_signals"] += 1
        p = candidate["primary_pattern"]
        detail = dict(
            signal_time=timestamp(decision),
            pattern=p["pattern"],
            name=p["name"],
            evidence_score=candidate["evidence_score"],
            volume_ratio=p["volume_ratio"],
            breakout_level=p["breakout_level"],
        )
        result, error = outcome(rows, index, horizon, fee_bps, slippage_bps)
        if result is None:
            counts[error] += 1
            if error == "pending_forward_data":
                pending.append(detail)
        else:
            trades.append(dict(**detail, **result))
    net = [r["net_return_pct"] for r in trades]
    summary = dict(
        completed_signals=len(trades),
        pending_signals=len(pending),
        positive_net_signals=sum(v > 0 for v in net),
        negative_net_signals=sum(v < 0 for v in net),
        zero_net_signals=sum(v == 0 for v in net),
        positive_net_rate_pct=None if not net else 100 * sum(v > 0 for v in net) / len(net),
        mean_net_return_pct=None if not net else mean(net),
        median_net_return_pct=None if not net else median(net),
        mean_gross_return_pct=None if not trades else mean(r["gross_return_pct"] for r in trades),
    )
    status = "ready"
    if len(rows) < 200:
        status = "insufficient_data"
    elif not counts["ready"]:
        status = "no_valid_windows"
    return dict(
        exchange="binance",
        market="spot",
        interval="15m",
        symbol=symbol,
        backtest_version="closed_window_replay_v1",
        experimental=True,
        as_of=timestamp(stamp),
        status=status,
        stored_candles_used=len(rows),
        warmup_bars=200,
        horizon_bars=horizon,
        fee_bps_per_side=fee_bps,
        slippage_bps_per_side=slippage_bps,
        min_volume_ratio=min_ratio,
        history_start=None if not rows else timestamp(rows[0].open_time),
        history_end=None if not rows else timestamp(rows[-1].open_time + BAR),
        window_counts={
            k: v
            for k, v in counts.items()
            if k in {"ready", "invalid_data", "missing_data", "stale_data", "insufficient_data"}
        },
        signal_counts={
            k: v
            for k, v in counts.items()
            if k not in {"ready", "invalid_data", "missing_data", "stale_data", "insufficient_data"}
        },
        summary=summary,
        signals=trades,
        pending=pending,
        note=(
            "Çakışabilen sinyallerin tarihsel sonuçlarıdır; "
            "portföy getirisi veya gelecek başarı olasılığı değildir."
        ),
    )


def backtest(
    db, symbol, stamp, history_limit=500, horizon=8, fee_bps=10, slippage_bps=5, min_ratio=1.5
):
    target = db.get(BinanceSpotSymbol, symbol)
    if target is None or not target.active:
        return None
    rows = list(
        reversed(
            db.query(BinanceSpotCandle)
            .filter(BinanceSpotCandle.symbol == symbol, BinanceSpotCandle.open_time + BAR <= stamp)
            .order_by(BinanceSpotCandle.open_time.desc())
            .limit(history_limit)
            .all()
        )
    )
    result = replay(rows, symbol, stamp, horizon, fee_bps, slippage_bps, min_ratio)
    result["history_limit"] = history_limit
    return result
