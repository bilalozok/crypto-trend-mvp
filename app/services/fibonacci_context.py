"""Descriptive retracement of the latest observable alternating pivot pair."""

import math

from app.services.formations import timestamp

RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


def levels(start, end):
    return [{"ratio": ratio, "price": end - (end - start) * ratio} for ratio in RATIOS]


def context(rows, bar):
    unavailable = {"status": "no_pivot_pair", "levels": [], "version": "fib_pivots3_v1"}
    if any(
        not all(math.isfinite(getattr(row, key, math.nan)) for key in ("high", "low"))
        or row.low <= 0
        or row.high < row.low
        for row in rows
    ):
        return unavailable
    pivots = []
    for i in range(3, len(rows) - 3):
        neighbours = rows[i - 3 : i] + rows[i + 1 : i + 4]
        high = all(rows[i].high > row.high for row in neighbours)
        low = all(rows[i].low < row.low for row in neighbours)
        # Outside bars can be both extrema; chronological order is unknown.
        if high == low:
            continue
        kind, price = ("high", rows[i].high) if high else ("low", rows[i].low)
        pivots.append((i, kind, price))
    if len(pivots) < 2:
        return unavailable
    end = pivots[-1]
    start = next((p for p in reversed(pivots[:-1]) if p[1] != end[1]), None)
    if start is None or (end[1] == "high" and end[2] <= start[2]):
        return unavailable
    if end[1] == "low" and end[2] >= start[2]:
        return unavailable

    def anchor(pivot):
        i, kind, price = pivot
        return dict(
            kind=kind,
            price=price,
            candle_close=timestamp(rows[i].open_time + bar),
            observable_at=timestamp(rows[i + 3].open_time + bar),
        )

    return dict(
        status="ready",
        version="fib_pivots3_v1",
        direction="up" if end[1] == "high" else "down",
        start=anchor(start),
        end=anchor(end),
        levels=levels(start[2], end[2]),
    )
