"""Directional pennants: a pole followed by a compact converging triangle."""

from app.services.formations import timestamp
from app.services.sloped_patterns import detect_sloped, fit

KINDS = {"bull_pennant", "bear_pennant"}


def detect_pennant(rows, highs, lows, kind, name, atr, tolerance, buffer):
    up = kind == "bull_pennant"
    direction = "up" if up else "down"

    def result_for(upper, lower):
        result = detect_sloped(
            rows,
            upper,
            lower,
            "symmetrical_triangle",
            name,
            atr,
            tolerance,
            buffer,
            expected_direction=direction,
        )
        result.update(
            pattern=kind,
            pole_points=[],
            pole_move_pct=None,
            pennant_retracement_ratio=None,
        )
        return result

    hp, lp = highs[-3:], lows[-3:]
    if len(hp) != 3 or len(lp) != 3:
        return result_for([], [])
    start, anchor = min(hp[0][0], lp[0][0]), max(hp[-1][0], lp[-1][0])
    if not (
        start >= 30
        and 12 <= anchor - start <= 48
        and len(rows) - anchor <= 30
        and (hp[0][0] < lp[0][0] if up else lp[0][0] < hp[0][0])
    ):
        return result_for([], [])
    upper, lower = fit(hp), fit(lp)
    if upper is None or lower is None:
        return result_for([], [])
    width = (upper[1] + upper[0] * start) - (lower[1] + lower[0] * start)
    indices = range(start - 30, start - 7)
    pole_index = (
        min(indices, key=lambda i: rows[i].close)
        if up
        else (max(indices, key=lambda i: rows[i].close))
    )
    pole_start = rows[pole_index].close
    pole_end = rows[start].high if up else rows[start].low
    move = pole_end - pole_start if up else pole_start - pole_end
    retreat = (
        pole_end - min(r.low for r in rows[start : anchor + 1])
        if up
        else (max(r.high for r in rows[start : anchor + 1]) - pole_end)
    )
    if width <= 0 or move < max(3 * atr, 3 * width) or not 0 <= retreat <= 0.6 * move:
        return result_for([], [])
    result = result_for(hp, lp)
    if result["status"] != "not_detected":
        result.update(
            pole_move_pct=(pole_end / pole_start - 1) * 100,
            pennant_retracement_ratio=retreat / move,
            pole_points=[
                {
                    "open_time": timestamp(rows[pole_index].open_time),
                    "price": pole_start,
                    "label": "Direk başlangıcı",
                },
                {
                    "open_time": timestamp(rows[start].open_time),
                    "price": pole_end,
                    "label": "Direk sonu",
                },
            ],
        )
    return result
