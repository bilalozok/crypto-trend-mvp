"""Ichimoku context, separate from archived indicator measurement rules."""

import math

from sqlalchemy import select

from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.portfolio_timeframe import PortfolioCandle
from app.services.formations import timestamp
from app.services.technical_indicators import INTERVALS
from app.services.technical_indicators import report as base_report


def calculate(rows, bar):
    if any(not math.isfinite(v) or v <= 0 for r in rows for v in (r.high, r.low, r.close)) or any(
        r.low > r.high or not r.low <= r.close <= r.high for r in rows
    ):
        return {"status": "invalid_data", "series": []}
    points = []
    for i, row in enumerate(rows):

        def midpoint(period, i=i):
            if i + 1 < period:
                return None
            window = rows[i + 1 - period : i + 1]
            return (max(r.high for r in window) + min(r.low for r in window)) / 2

        tenkan, kijun, span_b = midpoint(9), midpoint(26), midpoint(52)
        span_a = (tenkan + kijun) / 2 if kijun is not None else None
        prior = points[i - 26] if i >= 26 else {}
        points.append(
            dict(
                at=timestamp(row.open_time + bar),
                close=row.close,
                tenkan=tenkan,
                kijun=kijun,
                projected_a=span_a,
                projected_b=span_b,
                cloud_a=prior.get("projected_a"),
                cloud_b=prior.get("projected_b"),
            )
        )
    if not points or points[-1]["cloud_b"] is None:
        return {"status": "insufficient_data", "series": []}
    last = points[-1]
    top, bottom = max(last["cloud_a"], last["cloud_b"]), min(last["cloud_a"], last["cloud_b"])
    position = "above" if last["close"] > top else "below" if last["close"] < bottom else "inside"
    chikou_relation = "equal"
    if last["close"] > rows[-27].close:
        chikou_relation = "above"
    elif last["close"] < rows[-27].close:
        chikou_relation = "below"
    return dict(
        status="ready",
        series=points[-100:],
        latest=last,
        position=position,
        projected_at=timestamp(rows[-1].open_time + 27 * bar),
        chikou_reference=rows[-27].close,
        chikou_relation=chikou_relation,
    )


def report(db, symbol, stamp):
    result = base_report(db, symbol, stamp)
    if result is None:
        return None
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
            or any(b.open_time - a.open_time != bar for a, b in zip(rows, rows[1:], strict=False))
            or timestamp(rows[-1].open_time + bar) != h["latest"]["at"]
        ):
            h["ichimoku"] = {"status": "invalid_data", "series": []}
        else:
            h["ichimoku"] = calculate(rows, bar)
    return result
