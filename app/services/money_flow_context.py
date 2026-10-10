"""Closed-candle money flow and bounded candidate ranking context."""

import math
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.db.models.binance_spot import BinanceSpotCandle
from app.services.formations import BAR, timestamp
from app.services.ichimoku_context import calculate as cloud
from app.services.ichimoku_context import report as cloud_report

VERSION = "ichimoku_cmf21_mfi14_rank_v1"


def calculate(rows, bar):
    if any(
        not math.isfinite(v) or v < 0 for r in rows for v in (r.high, r.low, r.close, r.volume)
    ) or any(r.low <= 0 or r.high < r.low or not r.low <= r.close <= r.high for r in rows):
        return {"status": "invalid_data", "series": []}
    typical = [(r.high + r.low + r.close) / 3 for r in rows]
    signed = [0] + [
        (typical[i] > typical[i - 1]) - (typical[i] < typical[i - 1]) for i in range(1, len(rows))
    ]
    points = []
    for i, row in enumerate(rows):
        cmf = mfi = None
        if i >= 20:
            window = rows[i - 20 : i + 1]
            volume = sum(r.volume for r in window)
            if volume:
                cmf = (
                    sum(
                        (
                            (2 * r.close - r.high - r.low) / (r.high - r.low)
                            if r.high != r.low
                            else 0
                        )
                        * r.volume
                        for r in window
                    )
                    / volume
                )
        if i >= 14:
            indices = range(i - 13, i + 1)
            pos = sum(typical[j] * rows[j].volume for j in indices if signed[j] > 0)
            neg = sum(typical[j] * rows[j].volume for j in indices if signed[j] < 0)
            if pos + neg:
                mfi = 100 * pos / (pos + neg)
            elif sum(rows[j].volume for j in indices) > 0:
                mfi = 50.0
        points.append(dict(at=timestamp(row.open_time + bar), cmf=cmf, mfi=mfi))
    if not points or points[-1]["cmf"] is None or points[-1]["mfi"] is None:
        return {"status": "insufficient_data", "series": points[-100:]}
    return dict(status="ready", latest=points[-1], series=points[-100:])


def candidate_context(rows, stamp):
    if len(rows) < 200:
        return dict(version=VERSION, status="insufficient_data", adjustment=0)
    if (
        rows[-1].open_time + BAR != stamp // BAR * BAR
        or any(r.open_time % BAR or r.open_time + BAR > stamp for r in rows)
        or any(b.open_time - a.open_time != BAR for a, b in zip(rows, rows[1:], strict=False))
    ):
        return dict(version=VERSION, status="invalid_data", adjustment=0)
    c, f = cloud(rows, BAR), calculate(rows, BAR)
    if c["status"] != "ready" or f["status"] != "ready":
        return dict(version=VERSION, status="unavailable", adjustment=0)
    p = c["latest"]
    latest = f["latest"]
    trend = 0
    if c["position"] == "above" and p["tenkan"] > p["kijun"]:
        trend = 5
    elif c["position"] == "below" and p["tenkan"] < p["kijun"]:
        trend = -5
    flow = 0
    if latest["cmf"] > 0 and latest["mfi"] > 50:
        flow = 5
    elif latest["cmf"] < 0 and latest["mfi"] < 50:
        flow = -5
    return dict(
        version=VERSION,
        status="ready",
        candle_close_time=p["at"],
        ichimoku=dict(
            position=c["position"],
            tenkan=p["tenkan"],
            kijun=p["kijun"],
            cloud_a=p["cloud_a"],
            cloud_b=p["cloud_b"],
        ),
        cmf=latest["cmf"],
        mfi=latest["mfi"],
        mfi_extreme=latest["mfi"] >= 80 or latest["mfi"] <= 20,
        trend_adjustment=trend,
        flow_adjustment=flow,
        adjustment=trend + flow,
    )


def batch_context(db, symbols, stamp):
    if not symbols:
        return {}
    ranked = (
        select(
            BinanceSpotCandle,
            func.row_number()
            .over(
                partition_by=BinanceSpotCandle.symbol, order_by=BinanceSpotCandle.open_time.desc()
            )
            .label("position"),
        )
        .where(
            BinanceSpotCandle.symbol.in_(symbols),
            BinanceSpotCandle.interval == "15m",
            BinanceSpotCandle.open_time + BAR <= stamp,
        )
        .subquery()
    )
    model = aliased(BinanceSpotCandle, ranked)
    groups = defaultdict(list)
    for row in db.scalars(
        select(model).where(ranked.c.position <= 200).order_by(model.symbol, model.open_time)
    ):
        groups[row.symbol].append(row)
    return {symbol: candidate_context(groups[symbol], stamp) for symbol in symbols}


def report(db, symbol, stamp):
    result = cloud_report(db, symbol, stamp)
    if result is None:
        return None
    from app.db.models.portfolio_timeframe import PortfolioCandle
    from app.services.technical_indicators import INTERVALS

    for h in result["horizons"]:
        if h["status"] != "ready":
            continue
        bar = INTERVALS[h["interval"]]
        model = BinanceSpotCandle if h["interval"] == "15m" else PortfolioCandle
        rows = list(
            reversed(
                db.scalars(
                    select(model)
                    .where(
                        model.symbol == symbol,
                        model.interval == h["interval"],
                        model.open_time + bar <= stamp,
                    )
                    .order_by(model.open_time.desc())
                    .limit(200)
                ).all()
            )
        )
        if (
            len(rows) != 200
            or timestamp(rows[-1].open_time + bar) != h["latest"]["at"]
            or any(b.open_time - a.open_time != bar for a, b in zip(rows, rows[1:], strict=False))
        ):
            h["money_flow"] = {"status": "invalid_data", "series": []}
        else:
            h["money_flow"] = calculate(rows, bar)
    return result
