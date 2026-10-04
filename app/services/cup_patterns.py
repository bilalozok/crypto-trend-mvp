"""Experimental rounded cup and shallow handle structures."""

from app.services.formations import BAR, confirmation_volume, evaluate, timestamp

KINDS = {"cup_and_handle", "inverse_cup_and_handle"}


def detect_cup(rows, highs, lows, kind, name, tolerance, buffer):
    up = kind == "cup_and_handle"
    result = {
        "pattern": kind,
        "name": name,
        "status": "not_detected",
        "direction": "up" if up else "down",
        "breakout_level": None,
        "invalidation_level": None,
        "confirmation_buffer": buffer,
        "confirmed_at": None,
        "start_time": None,
        "anchor_time": None,
        "structure_available_at": None,
        "reason": "Yuvarlak gövde, yakın kenarlar ve sığ kulp bulunamadı.",
        "last_close": rows[-1].close,
        "last_candle_close_time": timestamp(rows[-1].open_time + BAR),
        "confirmation_age_bars": None,
        "distance_from_breakout_pct": None,
        "breakout_position": None,
        "breakout_holding": None,
        "confirmation_threshold": None,
        "pivot_points": [],
    }
    candidate = None
    rims = highs if up else lows
    handle_points = lows if up else highs
    sign = 1 if up else -1
    for left, right in zip(rims, rims[1:], strict=False):
        width = right[0] - left[0]
        if not 24 <= width <= 100 or abs(left[1] - right[1]) > tolerance:
            continue
        middle = range(left[0] + 1, right[0])
        center = min(middle, key=lambda i: sign * (rows[i].low if up else rows[i].high))
        fraction = (center - left[0]) / width
        if not 0.3 <= fraction <= 0.7:
            continue
        rim = min(sign * left[1], sign * right[1])
        bottom = sign * (rows[center].low if up else rows[center].high)
        depth = rim - bottom
        if depth < 4 * tolerance:
            continue
        # Several candles near the bottom distinguish a bowl from a single V.
        central = [sign * r.close for r in rows[center - 3 : center + 4]]
        if sum(v <= bottom + depth * 0.35 for v in central) < 5:
            continue
        # Both shoulders must spend time between the bottom and the rims.
        shoulders = [left[0] + width // 4, left[0] + 3 * width // 4]
        if any(
            not bottom + depth * 0.2 <= sign * rows[i].close <= rim - depth * 0.1 for i in shoulders
        ):
            continue
        if any(sign * r.close > rim + tolerance for r in rows[left[0] : right[0] + 1]):
            continue
        for handle in handle_points:
            duration = handle[0] - right[0]
            if not 4 <= duration <= min(24, width // 2) or len(rows) - handle[0] > 30:
                continue
            retracement = (sign * right[1] - sign * handle[1]) / depth
            if not 0.1 <= retracement <= 0.5:
                continue
            segment = rows[right[0] + 1 : handle[0] + 1]
            if any(sign * r.close > rim + tolerance for r in segment):
                continue
            level = max(left[1], right[1]) if up else min(left[1], right[1])
            floor = handle[1]
            selected = [
                (left[0], left[1], "high" if up else "low"),
                (center, sign * bottom, "low" if up else "high"),
                (right[0], right[1], "high" if up else "low"),
                (handle[0], handle[1], "low" if up else "high"),
            ]
            item = (left[0], handle[0], selected, level, floor, depth, retracement)
            if candidate is None or handle[0] >= candidate[1]:
                candidate = item
    if candidate is not None:
        start, anchor, selected, level, floor, depth, retracement = candidate
        status, confirmed = evaluate(rows, anchor, up, level, floor, buffer)
        position = "within_buffer"
        if rows[-1].close > level + buffer:
            position = "above"
        elif rows[-1].close < level - buffer:
            position = "below"
        result.update(
            status=status,
            cup_depth=depth,
            handle_retracement_ratio=retracement,
            breakout_level=level,
            invalidation_level=floor,
            confirmed_at=confirmed,
            start_time=timestamp(rows[start].open_time),
            anchor_time=timestamp(rows[anchor].open_time),
            structure_available_at=timestamp(rows[anchor + 3].open_time + BAR),
            confirmation_age_bars=(
                None
                if confirmed is None
                else (rows[-1].open_time + BAR - round(confirmed.timestamp() * 1000)) // BAR
            ),
            distance_from_breakout_pct=(rows[-1].close / level - 1) * 100,
            breakout_position=position,
            breakout_holding=position == ("above" if up else "below"),
            confirmation_threshold=level + buffer if up else level - buffer,
            reason={
                "forming": "Fincan ve kulp bulundu; kenar kırılımı bekleniyor.",
                "confirmed": "Fincanın iki kenarı kapanışla ve teyit payıyla aşıldı.",
                "invalidated": "Kulp sınırı kapanışla aşıldı.",
            }[status],
            pivot_points=[
                {
                    "open_time": timestamp(rows[i].open_time),
                    "price": v,
                    "kind": k,
                    "label": "Fincan/kulp",
                }
                for i, v, k in selected
            ],
        )
    result.update(confirmation_volume(rows, result["confirmed_at"]))
    return result
